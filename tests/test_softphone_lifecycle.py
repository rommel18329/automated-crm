"""Run the shipped browser handlers with simulated out-of-order SDK events."""
import ast
from pathlib import Path
import re
import shutil
import subprocess


def softphone_js():
    tree = ast.parse(Path('webhook_server.py').read_text(encoding='utf-8'))
    page = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'softphone_page')
    html = next(ast.literal_eval(n.value) for n in page.body
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'html' for t in n.targets))
    return re.search(r'<script>(.*?)</script>', html, re.S).group(1)


def function(js, name):
    # Functions in this script are indented two spaces; their closing brace
    # has that same indentation. Nested callbacks are indented further.
    return re.search(r'^  (?:async )?function '+name+r'\(.*?^  }', js, re.M | re.S).group(0)


def test_softphone_script_syntax_and_late_events(tmp_path):
    node = shutil.which('node')
    assert node, 'Node is required to validate the softphone browser code'
    js = softphone_js()
    script = tmp_path / 'softphone.js'
    script.write_text(js, encoding='utf-8')
    subprocess.run([node, '--check', str(script)], check=True, capture_output=True, text=True)
    harness = r'''
const assert = require('node:assert/strict');
let call=null, connectPending=false, callGeneration=0, currentBatchId=null;
let callEndHandled=false, acceptedFired=false, joinToken='token', ended=0;
let nextTimer=null, batchPollInterval=null, btnHangup={disabled:true}, btnMute={disabled:true};
let events=[], requests=[], timers=[];
let device={disconnectAll(){events.push('disconnectAll')}};
function logMsg(s){events.push(s)}
function setStatus(s){events.push(s)}
function startTimer(){}
function stopRinging(){}
function handleCallEnd(){ended++;call=null;btnHangup.disabled=true;callGeneration++}
function onCallEnded(){handleCallEnd()}
function fakeCall(){return {handlers:{},on(n,fn){this.handlers[n]=fn},disconnect(){this.disconnected=true}}}
function fetch(url){requests.push(url);return Promise.resolve({json:()=>Promise.resolve({owner:true})})}
function setInterval(fn){timers.push(fn);return timers.length}
function clearInterval(){}
function setTimeout(fn){timers.push(fn);return timers.length}
function clearTimeout(){}
'''
    harness += function(js, 'wireCall') + '\n' + function(js, 'hangUp')
    harness += r'''
const old=fakeCall(), fresh=fakeCall();
callGeneration=1;wireCall(old,'old-batch',1);
callGeneration=2;wireCall(fresh,'new-batch',2);
old.handlers.disconnect();old.handlers.error({message:'late'});old.handlers.accept();
assert.equal(call,fresh);assert.equal(ended,0);assert.equal(requests.length,0);
currentBatchId=null; // the handler must use its own immutable batch ID
fresh.handlers.accept();
assert.equal(btnHangup.disabled,false);
assert.deepEqual(requests,['/dialer/agent-ready/new-batch']);
assert.ok(!events.includes('Call connected'));
fresh.handlers.disconnect();fresh.handlers.disconnect();
assert.equal(ended,1);
connectPending=true;hangUp();
assert.equal(connectPending,false);assert.ok(events.includes('disconnectAll'));
'''
    # Pending Device.connect must block a second join, and hang-up must reject
    # its late result rather than resurrecting an orphaned call.
    harness += function(js, 'joinMulti')
    harness += r'''
let lastDialTs=null,currentLeadId=null,dispBatchId=null,lastCallDuration=0;
let nameEl={},phoneEl={},timerEl={},btnCall={},autoAdvance=true;
function hideDisposition(){}
function loadScriptForLead(){}
function startRinging(){}
function updateQueueBar(){}
let resolveConnect, connectCount=0;
device.connect=()=>{connectCount++;return new Promise(resolve=>resolveConnect=resolve)};
fetch=async()=>({json:async()=>({status:'waiting_agent'})});
(async()=>{
 call=null;
 const a=joinMulti({ts:'a',batch_id:'a',conference:'a',lines:10});
 await Promise.resolve();await Promise.resolve();await Promise.resolve();
 await joinMulti({ts:'b',batch_id:'b',conference:'b',lines:10});
 assert.equal(connectCount,1);
 hangUp();
 const late=fakeCall();resolveConnect(late);await a;
 assert.equal(late.disconnected,true);assert.equal(call,null);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    script.write_text(harness, encoding='utf-8')
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_agent_join_timeout_returns_undialed_leads():
    import logging
    import threading
    from datetime import datetime
    from types import SimpleNamespace

    tree = ast.parse(Path('webhook_server.py').read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_start_multi_batch')
    workers, recovered = [], []
    class Worker:
        def __init__(self, target, **kwargs):
            workers.append(target)
        def start(self):
            pass
    namespace = dict(uuid=SimpleNamespace(uuid4=lambda: SimpleNamespace(hex='testbatch123')),
                     _batch_lock=threading.Lock(), _multi_batches={}, _dialer_current={},
                     threading=SimpleNamespace(Thread=Worker), datetime=datetime,
                     time=SimpleNamespace(sleep=lambda seconds: None),
                     log=logging.getLogger(__name__),
                     _requeue_leads=lambda agent, ids: recovered.append((agent, ids)))
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<batch>', 'exec'), namespace)
    batch_id = namespace['_start_multi_batch']('rom', [1, 2, 3])
    workers[0]()
    assert recovered == [('rom', [1, 2, 3])]
    assert namespace['_multi_batches'][batch_id]['status'] == 'no_agent'
    assert namespace['_dialer_current'] == {}
