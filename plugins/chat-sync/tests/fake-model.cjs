const http=require('node:http');
module.exports=async function start(){
 const server=http.createServer((req,res)=>{
  const chunks=[];req.on('data',c=>chunks.push(c));req.on('end',()=>{
   if(!req.url.includes('/responses')){res.writeHead(200,{'content-type':'application/json'});res.end('{"data":[]}');return;}
   const raw=Buffer.concat(chunks).toString();
   const second=raw.includes('Second metadata check');
   const answer=second?'Birch metadata verified.':'Cedar metadata verified.';
   const id='resp_'+Date.now(),message={id:'msg_'+Date.now(),type:'message',status:'completed',role:'assistant',phase:'final_answer',content:[{type:'output_text',text:answer,annotations:[]}]};
   const response={id,object:'response',created_at:Math.floor(Date.now()/1000),model:'offline-test',status:'completed',output:[message],usage:{input_tokens:second?200:100,input_tokens_details:{cached_tokens:20},output_tokens:second?25:15,output_tokens_details:{reasoning_tokens:5},total_tokens:second?225:115}};
   res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});
   let seq=0;const evt=(type,data)=>res.write('event: '+type+'\ndata: '+JSON.stringify({type,sequence_number:seq++,...data})+'\n\n');
   evt('response.created',{response:{...response,status:'in_progress',usage:null,output:[]}});
   evt('response.output_item.added',{output_index:0,item:{...message,status:'in_progress',content:[]}});
   evt('response.output_text.delta',{output_index:0,item_id:message.id,content_index:0,delta:answer});
   evt('response.output_item.done',{output_index:0,item:message});
   evt('response.completed',{response});res.end();
  });
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));server.unref();return server.address().port;
};
