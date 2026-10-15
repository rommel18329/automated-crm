import ast
from datetime import datetime, date, timedelta
import logging
from pathlib import Path
import re
import shutil
import subprocess
import threading

from flask import Flask, request, Response, jsonify
from crm_engine import db


def test_investor_disposition_and_live_note(tmp_path):
    path = tmp_path / 'crm.db'
    db.init_db(path)
    lead_id = db.create_lead('Edin test', '5155550100', path,
                             conversation_stage='not_contacted', next_action_date='2026-12-01')
    tree = ast.parse(Path('webhook_server.py').read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if
             (isinstance(n, ast.FunctionDef) and n.name in ('dialer_dispose','dialer_note')) or
             (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_DISPOSITIONS' for t in n.targets))]
    app = Flask(__name__)
    ns = dict(app=app, db=db, DB_PATH=path, request=request, Response=Response,
              jsonify=jsonify, datetime=datetime, date=date, timedelta=timedelta,
              _cors=lambda response: response, _batch_lock=threading.Lock(),
              _multi_batches={}, _lead_claims={}, log=logging.getLogger(__name__))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<routes>','exec'),ns)
    client = app.test_client()
    # Saving during a call does not require a disposition or alter classification.
    result = client.post('/dialer/note', json={'lead_id':lead_id,'note':'Buyer criteria','agent':'Rom'})
    assert result.json['ok']
    assert db.fetch_lead(lead_id,path)['status'] == 'cold'
    assert db.fetch_interactions(lead_id,path)[0]['content'] == '📞 Rom: Buyer criteria'
    result = client.post('/dialer/dispose', json={'lead_id':lead_id,'disposition':'Investor','agent':'Rom'})
    assert result.json['ok']
    lead = db.fetch_lead(lead_id,path)
    assert lead['status'] == 'investor'
    assert lead['conversation_stage'] == 'initial_contact'
    assert lead['last_contact_date'] == date.today().isoformat()
    assert lead['next_action_date'] == '2026-12-01'
    with db.get_conn(path) as conn:
        row=conn.execute('SELECT disposition,status_set FROM dispositions').fetchone()
    assert tuple(row) == ('Investor','investor')
    assert client.post('/dialer/note',json={'lead_id':99999,'note':'wrong lead'}).status_code == 404


def test_live_note_save_failures_edits_and_hangup(tmp_path):
    tree = ast.parse(Path('webhook_server.py').read_text(encoding='utf-8'))
    page = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='softphone_page')
    html = next(ast.literal_eval(n.value) for n in page.body if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='html' for t in n.targets))
    js = re.search(r'<script>(.*?)</script>',html,re.S).group(1)
    names=['bindCallNotes','hasUnsavedNote','saveCallNote','saveNoteThenNext','hideDisposition']
    functions='\n'.join(re.search(r'^  (?:async )?function '+name+r'\(.*?^  }',js,re.M|re.S).group(0) for name in names)
    harness=r'''
const assert=require('node:assert/strict');
let noteLeadId=null,noteSavePending=false,noteDrafts={},dispNote={value:''};
let liveNotes={style:{}},btnSaveNote={},btnInvestor={},noteSaveStatus={},repNameEl={value:'Rom'};
let currentLeadId=1,call={},connectPending=false,nextTimer=null,awaitingDisposition=true;
let queueActive=true,autoAdvance=true,scheduled=0,requests=[];
let savedParts=[],outcomeSaved=false,statusSaved=false,lastOutcomeNoStatus=false;
const element=()=>({classList:{remove(){}},textContent:''});
let dispEl=element(),dispDoneEl=element(),dispOutcomeEl=element(),dispStatusEl=element();
const document={getElementById:()=>({style:{}})};
function scheduleNext(){scheduled++}
function logMsg(){}
function setStatus(){}
let fetch=async(url,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>({ok:true})}};
'''+functions+r'''
(async()=>{
 bindCallNotes(1);dispNote.value='Live note';
 const activeCall=call;
 await saveCallNote();
 assert.equal(call,activeCall);assert.equal(scheduled,0);
 assert.equal(requests[0].lead_id,1);assert.equal(dispNote.value,'');
 dispNote.value='Keep through hangup';call=null;hideDisposition();
 assert.equal(dispNote.value,'Keep through hangup');
 fetch=async()=>({ok:false,json:async()=>({ok:false,error:'DB unavailable'})});
 await saveNoteThenNext();
 assert.equal(scheduled,0);assert.equal(dispNote.value,'Keep through hangup');
 assert.ok(noteSaveStatus.textContent.includes('Save failed'));
 fetch=async()=>{throw new Error('offline')};
 await saveNoteThenNext();assert.equal(scheduled,0);
 let resolve;
 fetch=()=>new Promise(r=>resolve=r);
 dispNote.value='First';
 const saving=saveCallNote();
 assert.equal(noteSavePending,true);
 assert.equal(await saveCallNote(),false);
 dispNote.value='First and second';
 resolve({ok:true,json:async()=>({ok:true})});await saving;
 assert.equal(dispNote.value,' and second');
 // A response for an old lead must never clear the new lead's draft.
 dispNote.value='Old lead';const oldSave=saveCallNote();
 bindCallNotes(2);currentLeadId=2;dispNote.value='New lead';
 resolve({ok:true,json:async()=>({ok:true})});await oldSave;
 assert.equal(dispNote.value,'New lead');assert.equal(noteDrafts[1],'');
 fetch=async()=>({ok:true,json:async()=>({ok:true})});
 call={};await saveNoteThenNext();assert.equal(scheduled,0);
 call=null;await saveNoteThenNext();assert.equal(scheduled,1);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    script=tmp_path/'notes.js';script.write_text(harness,encoding='utf-8')
    result=subprocess.run([shutil.which('node'),str(script)],capture_output=True,text=True,timeout=15)
    assert result.returncode == 0,result.stderr
