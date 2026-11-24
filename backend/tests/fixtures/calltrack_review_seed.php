<?php
declare(strict_types=1);
// CI-only standalone seed. Guard both DB hostname and disposable DB name.
if(getenv('CALLTRACK_E2E_FIXTURE')!=='1')throw new RuntimeException('CI fixture only');
if(getenv('ANALYTICS_TASK_TEST_DSN')!=='mysql:host=analytics-test-mariadb;dbname=analytics_http_test')
 throw new RuntimeException('Disposable DB required');
require_once __DIR__.'/../../../calltrack/api/analytics_sync.php';
require_once __DIR__.'/../../../calltrack/tests/fixtures/analytics/database.php';
$db=analyticsTestDatabase();
$admin=['id'=>1,'role'=>'admin'];
saveManagerBinding($db,$admin,'Fixture owner three',3,null);
$event=['source'=>'sales-journal','signal_id'=>'history-e2e','revision'=>1,
 'status'=>'ACTIVE','signal_type'=>'CHURN_RISK','family'=>'INACTIVITY',
 'subject'=>['kind'=>'normalized_name','version'=>1,'key'=>'ооо радуга'],
 'score'=>70,'confidence'=>0.8,'as_of'=>'2026-11-20',
 'last_purchase'=>'2026-10-01','calculated_at'=>'2026-11-21T10:00:00Z',
 'mode'=>'SHADOW','automation_allowed'=>false,'metrics'=>[],
 'run'=>['id'=>'e2e-run','scope'=>[
   'policy'=>'aviatorov-wholesale-corporate/2',
   'department'=>'Авиаторов','buyer_types'=>['Оптовик','Корпоративный']]]];
analyticsValidateEvent($event);
$key=analyticsSignalKey($event);
$json=json_encode($event,JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR);
$subject=analyticsSubjectKey($event['subject'],$event['run']['scope']);
$db->prepare('INSERT INTO analytics_inbox(signal_key,source,signal_id,signal_revision,payload_hash,payload_json,subject_key,decision,reasons_json) VALUES(?,?,?,?,?,?,?,?,?)')
 ->execute([$key,'sales-journal','history-e2e',1,hash('sha256',$json),$json,$subject,'SHADOW','[]']);
$storage=rtrim(getenv('CALLTRACK_STORAGE_DIR'),'/');
@mkdir($storage.'/cache/clients',0770,true);
file_put_contents($storage.'/cache/clients/clients.json',json_encode([
 ['id'=>'synthetic-client-100','name'=>'ООО РАДУГА',
  'phones'=>['+79990000000'],
  'fields'=>['manager'=>'Fixture owner three','Менеджер'=>'Fixture owner three',
             'status'=>'active']]
],JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR));
require_once __DIR__.'/../../../calltrack/api/web_auth.php';
ini_set('session.save_path',$storage.'/sessions');
@mkdir($storage.'/sessions',0770,true);
startWebSession();
$_SESSION['web_user_id']=1;
$_SESSION['created_at']=time();
$_SESSION['last_seen_at']=time();
$csrf=webCsrfToken();
echo json_encode(['signal_key'=>$key,'session_id'=>session_id(),'csrf'=>$csrf],JSON_THROW_ON_ERROR).PHP_EOL;
session_write_close();
