<?php
declare(strict_types=1);
/** Local stdin/stdout transport. No new HTTP task-creation endpoint. */
if (PHP_SAPI!=='cli') { http_response_code(404);exit; }
require_once __DIR__.'/config.php';
require_once __DIR__.'/analytics_sync.php';
require_once __DIR__.'/analytics_permanent.php';
require_once __DIR__.'/analytics_verified_bindings.php';
try {
    $command=$argv[1]??'';
    if (!in_array($command,['health','preflight','deliver','policy'],true)) throw new DomainException('INVALID_COMMAND');
    $raw=stream_get_contents(STDIN,4194305);
    if ($raw===false || strlen($raw)>4194304) throw new DomainException('INPUT_TOO_LARGE');
    $input=json_decode($raw,true,64,JSON_THROW_ON_ERROR);
    $pdo=getPdo();
    if ($command==='health') {
        if ((int)$pdo->query('SELECT 1')->fetchColumn()!==1) throw new DomainException('RECEIVER_UNAVAILABLE');
        echo '{"receiver":"reachable"}'.PHP_EOL;exit;
    }
    if ($command==='policy') {
        echo json_encode(saveAnalyticsPermanentPolicy($pdo,$input,$argv[2]??''),JSON_THROW_ON_ERROR).PHP_EOL;exit;
    }
    if (analyticsIntegrationMode()!=='enabled') throw new DomainException('RECEIVER_DISABLED');
    if (!is_array($input['events']??null)||!array_is_list($input['events'])||count($input['events'])<1||count($input['events'])>25) throw new DomainException('INVALID_PAGE');
    foreach (['job_id','release_id'] as $key) if (!is_string($input[$key]??null)||!preg_match('/^[a-zA-Z0-9-]{1,36}$/D',$input[$key])) throw new DomainException('INVALID_CONTEXT');
    $s=$pdo->prepare('SELECT GET_LOCK(?,0)');$lock='analytics-permanent-consumer';$s->execute([$lock]);
    if ((int)$s->fetchColumn()!==1) throw new DomainException('CONSUMER_BUSY');
    try {
        $refreshLock=fopen(clientsEnsureDirectory(clientsProjectStorageDirectory()).'/clients_cache_refresh.lock','c');
        if (!$refreshLock || !flock($refreshLock,LOCK_SH|LOCK_NB)) throw new DomainException('CLIENTS_REFRESH_IN_PROGRESS');
        try {
        $results=[];$validated=[];$ids=[];
        foreach ($input['events'] as $item) {
            // Hash the producer's exact canonical bytes. PHP/Python float rendering
            // differs (1.0 vs 1, exponent formatting); never reserialize to compare.
            $raw=$item['event_json']??null;
            if (!is_string($raw) || strlen($raw)>65536) throw new DomainException('INVALID_EVENT_BYTES');
            $event=json_decode($raw,true,64,JSON_THROW_ON_ERROR);
            if (!is_int($item['event_id']??null)||!is_string($item['event_hash']??null)
                || hash('sha256',$raw)!==$item['event_hash']
                || ($event['run']['id']??null)!==$input['release_id'] || !isset($event['permanent_policy'])) throw new DomainException('EVENT_CONTEXT_MISMATCH');
            analyticsValidateEvent($event);
            $binding=analyticsBinding($pdo,analyticsSubjectKey($event['subject'],$event['run']['scope']));
            if ($binding && !empty($binding['client_id'])) $ids[(string)$binding['client_id']]=(string)$binding['client_id'];
            $validated[]=[$item,$event,$binding];
        }
        $clients=analyticsDirectoryRecords(array_values($ids));
        $policy=analyticsIntegrationPolicy($pdo);$policy['cache_revision']=clientCatalogRevision();
        // One bounded local-directory pass for the entire page, no upstream import.
        analyticsPrimeReviewGroups(array_column($validated,1),managerBindingRows($pdo),$policy['cache_revision']);
        foreach ($validated as [$item,$event,$binding]) {
            $result=processAnalyticsEvent($pdo,$event,$clients[(string)($binding['client_id']??'')]??null,$policy,$command==='deliver'?'enabled':'dry_run',time());
            $result['created_for_release']=false;
            if (!empty($result['task_id'])) {
                $task=$pdo->prepare('SELECT source_signal_key,evidence_json FROM manager_tasks WHERE id=?');$task->execute([$result['task_id']]);$stored=$task->fetch(PDO::FETCH_ASSOC);
                $proof=$stored?json_decode($stored['evidence_json'],true,64,JSON_THROW_ON_ERROR):[];
                $result['created_for_release']=$stored && $stored['source_signal_key']===analyticsSignalKey($event)
                    && ($proof['signal']['run']['id']??null)===$input['release_id'];
            }
            $results[]=array_merge(['event_id'=>$item['event_id'],'event_hash'=>$item['event_hash']],$result);
        }
        echo json_encode(['job_id'=>$input['job_id'],'release_id'=>$input['release_id'],'receipts'=>$results],JSON_THROW_ON_ERROR).PHP_EOL;
        } finally { if (is_resource($refreshLock)) { flock($refreshLock,LOCK_UN);fclose($refreshLock); } }
    } finally { $pdo->prepare('SELECT RELEASE_LOCK(?)')->execute([$lock]); }
} catch (Throwable $e) { fwrite(STDERR,"Permanent churn command failed; no success acknowledged\n");exit(1); }
