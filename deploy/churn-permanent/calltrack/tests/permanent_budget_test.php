<?php
declare(strict_types=1);
/** Disposable SQLite only. Never calls getPdo(), network, or a live Calltrack. */
$root=getenv('CALLTRACK_PERMANENT_TEST_ROOT');
if (!$root || !str_ends_with($root,'calltrack-permanent-test')) throw new RuntimeException('Isolated patched checkout required');
require_once $root.'/api/analytics_permanent.php';
require_once $root.'/api/manager_tasks_service.php';
require_once $root.'/api/analytics_task_policy.php';
require_once $root.'/tests/fixtures/analytics/database.php';
function check(bool $v,string $m):void { if (!$v) throw new RuntimeException($m); }
function blocks(callable $f):void { try { $f(); } catch (Throwable $e) { return; } throw new RuntimeException('Expected a blocked operation'); }
$db=analyticsTestDatabase();
$db->exec('CREATE TABLE analytics_permanent_policy(id INTEGER PRIMARY KEY,revision INTEGER,approved INTEGER,enabled INTEGER,daily_limit INTEGER,cooldown_days INTEGER,producer_revision INTEGER,generator_version TEXT)');
$db->exec("INSERT INTO analytics_permanent_policy VALUES(1,0,0,0,0,0,0,'')");
$db->exec('CREATE TABLE analytics_permanent_reservations(signal_key TEXT PRIMARY KEY,business_date TEXT,policy_revision INTEGER)');
$db->exec('CREATE TABLE analytics_permanent_policy_events(id INTEGER PRIMARY KEY,actor TEXT,revision INTEGER,action TEXT,policy_json TEXT)');
$db->exec('UPDATE analytics_task_budget SET max_tasks=10,used_tasks=10');
$historical=$db->query('SELECT * FROM analytics_task_budget')->fetch(PDO::FETCH_ASSOC);
$draft=['action'=>'draft','revision'=>0,'daily_limit'=>2,'cooldown_days'=>30,'producer_revision'=>2,'generator_version'=>'inactivity/aviator-churn-pilot/2'];
saveAnalyticsPermanentPolicy($db,$draft,'synthetic-operator');
blocks(fn()=>saveAnalyticsPermanentPolicy($db,['action'=>'enable','revision'=>1],'operator'));
blocks(fn()=>saveAnalyticsPermanentPolicy($db,['action'=>'approve','revision'=>1,'confirm_business_approval'=>1],'operator'));
saveAnalyticsPermanentPolicy($db,['action'=>'approve','revision'=>1,'confirm_business_approval'=>true],'operator');
check((int)$db->query('SELECT enabled FROM analytics_permanent_policy')->fetchColumn()===0,'approval does not enable');
saveAnalyticsPermanentPolicy($db,['action'=>'enable','revision'=>2],'operator');
$event=['source'=>'sales-journal','signal_id'=>'signal-1','generator_version'=>'inactivity/aviator-churn-pilot/2',
 'last_purchase'=>'2026-07-01','subject'=>['kind'=>'external_id','key'=>'synthetic-client'],
 'assignment'=>['client_id'=>'synthetic-client','manager_id'=>3],
 'signal_type'=>'CHURN_RISK','mode'=>'AUTOMATION_APPROVED','automation_allowed'=>true,'status'=>'ACTIVE',
 'permanent_policy'=>['version'=>'churn-permanent/1','revision'=>2,'producer_revision'=>2,'daily_limit'=>2,'cooldown_days'=>30]];
// Rollback of a failed create also rolls back its budget reservation.
$db->beginTransaction();analyticsPermanentBudget($db,$event,true);$db->rollBack();
check((int)$db->query('SELECT COUNT(*) FROM analytics_permanent_reservations')->fetchColumn()===0,'failed creation reservation rolled back');
$db->beginTransaction();analyticsPermanentBudget($db,$event,true);$db->commit();
$db->beginTransaction();blocks(fn()=>analyticsPermanentBudget($db,$event,true));$db->rollBack();
check((int)$db->query('SELECT COUNT(*) FROM analytics_permanent_reservations')->fetchColumn()===1,'duplicate reservation rejected');
$second=$event;$second['signal_id']='signal-2';$second['subject']['key']='synthetic-client-2';
$db->beginTransaction();analyticsPermanentBudget($db,$second,true);$db->commit();
$db->beginTransaction();blocks(fn()=>analyticsPermanentBudget($db,$second));$db->rollBack();
check($db->query('SELECT * FROM analytics_task_budget')->fetch(PDO::FETCH_ASSOC)===$historical,'pilot remains exhausted and unchanged');
// A separate fresh SQLite budget demonstrates task idempotency through the actual service.
$db->exec('DELETE FROM analytics_permanent_reservations');
saveAnalyticsTaskPolicy($db,['id'=>1,'role'=>'admin'],['revision'=>1,'action'=>'save_draft','min_confidence'=>0.5,'types'=>[
 ['signal_type'=>'CHURN_RISK','enabled'=>true,'priority'=>'HIGH','due_days'=>3],
 ['signal_type'=>'PURCHASE_OVERDUE','enabled'=>false,'priority'=>'NORMAL','due_days'=>3]]]);
saveAnalyticsTaskPolicy($db,['id'=>1,'role'=>'admin'],['revision'=>2,'action'=>'approve','confirm_business_approval'=>true]);
$automatic=['allowed'=>true,'signal_key'=>hash('sha256','synthetic-key'),'evidence'=>['signal'=>$event]];
$input=['client_id'=>'synthetic-client','assignee_id'=>3,'family'=>'INACTIVITY','title'=>'Synthetic task','priority'=>'HIGH'];
$first=createManagerTask($db,[],$input,$automatic);$replay=createManagerTask($db,[],$input,$automatic);
check(!$first['existing']&&$replay['existing']&&$first['task']['id']===$replay['task']['id'],'atomic service replay');
check((int)$db->query('SELECT COUNT(*) FROM analytics_permanent_reservations')->fetchColumn()===1,'replay consumes no new budget');
updateManagerTask($db,['id'=>3,'role'=>'manager'],$first['task']['id'],['version'=>1,'status'=>'DONE','result'=>'Synthetic completion']);
$same=$automatic;$same['signal_key']=hash('sha256','new-signal-same-anchor');$same['evidence']['signal']['signal_id']='new-id';
$closed=createManagerTask($db,[],$input,$same);
check($closed['existing']&&$closed['task']['status']==='DONE','closed episode is not reopened');
$new=$automatic;$new['signal_key']=hash('sha256','new-purchase');$new['evidence']['signal']['signal_id']='new-purchase';$new['evidence']['signal']['last_purchase']='2026-08-01';
blocks(fn()=>createManagerTask($db,[],$input,$new));
check((int)$db->query('SELECT COUNT(*) FROM manager_tasks')->fetchColumn()===1,'cooldown blocks new purchase until approved interval');
$db->exec("UPDATE manager_tasks SET created_at='2020-01-01 00:00:00'");
$after=createManagerTask($db,[],$input,$new);
check(!$after['existing'],'new purchase after cooldown creates new episode');
check($db->query('SELECT * FROM analytics_task_budget')->fetch(PDO::FETCH_ASSOC)===$historical,'new mode never consumes or refunds pilot budget');
saveAnalyticsPermanentPolicy($db,['action'=>'disable','revision'=>2],'operator');
$db->beginTransaction();blocks(fn()=>analyticsPermanentPolicy($db,$event));$db->rollBack();
echo "permanent_budget_test: policy, revocation, rollback, daily cap, pilot preservation, replay, closed episode, cooldown OK\n";
