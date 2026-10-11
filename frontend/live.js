(function(){
  var U=window.__US,D=window.__DASH;if(!U||!D)return;
  var DMap=U.DMap,WM=U.WM,proj=U.proj,unproj=U.unproj,toast=U.toast,modal=U.modal,segBind=U.segBind,sevChip=U.sevChip,
      legendRamp=U.legendRamp,openTab=U.openTab,ic=U.ic,HEAT=U.HEAT,WATER=U.WATER,pick=U.pick,INIT=U.INIT,inited=U.inited,
      el=U.el,$=U.$,$$=U.$$,NS='http://www.w3.org/2000/svg';

  /* ---------- where the API lives ---------- */
  function apiBase(){
    try{var q=new URLSearchParams(location.search).get('api');if(q){localStorage.setItem('us_api',q);return q.replace(/\/$/,'');}}catch(e){}
    try{var s=localStorage.getItem('us_api');if(s)return s.replace(/\/$/,'');}catch(e){}
    if(/^https?:$/.test(location.protocol)&&!/claude|anthropic|usercontent/.test(location.hostname))return location.origin;
    return null;}
  var API=apiBase(),chip=$('#liveChip');
  function setChip(state,text){chip.className='live-chip '+(state||'');chip.querySelector('em').textContent=text;}
  if(!API){setChip('','Example data');return;}
  var DEMO={};Object.keys(INIT).forEach(function(k){DEMO[k]=INIT[k];});

  /* ---------- helpers ---------- */
  function get(path,opts){opts=opts||{};var ctl=new AbortController(),t=setTimeout(function(){ctl.abort();},opts.timeout||25000);
    var init={method:opts.method||'GET',headers:opts.headers||{},body:opts.body,signal:ctl.signal};
    return fetch(API+path,init).then(function(r){
      var ct=r.headers.get('content-type')||'';
      if(!r.ok)return (ct.indexOf('json')>=0?r.json():r.text()).then(function(j){var m=j&&j.detail?(typeof j.detail==='string'?j.detail:j.detail.map?j.detail.map(function(x){return x.msg;}).join('; '):JSON.stringify(j.detail)):('HTTP '+r.status);var e=new Error(m);e.status=r.status;throw e;});
      return ct.indexOf('json')>=0?r.json():r.text();
    },function(e){var er=new Error(e.name==='AbortError'?'The server took too long to answer':'Cannot reach the Raहत server');er.status=0;throw er;})
    .finally(function(){clearTimeout(t);});}
  function post(path,body){return get(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
  var TOK={get:function(){try{return localStorage.getItem('us_tok')||'';}catch(e){return '';}},set:function(v){try{localStorage.setItem('us_tok',v);}catch(e){}},clear:function(){try{localStorage.removeItem('us_tok');}catch(e){}}};
  function off(path,opts){opts=opts||{};var h=opts.headers||{};h.Authorization='Bearer '+TOK.get();opts.headers=h;
    return get(path,opts).catch(function(e){if(e.status===401){TOK.clear();toast('Officer sign-in expired, sign in again');}throw e;});}
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function parseT(s){if(!s)return null;return new Date(/[zZ]$|[+-]\d\d:?\d\d$/.test(s)?s:s+'Z');}
  function ago(s){var d=parseT(s);if(!d)return '';var m=Math.round((Date.now()-d)/60000);if(m<1)return 'just now';if(m<60)return m+' min ago';var h=Math.round(m/60);if(h<24)return h+' h ago';return Math.round(h/24)+' d ago';}
  var TZ='Asia/Kolkata';
  function hm(d){return d.toLocaleTimeString('en-IN',{hour:'numeric',minute:'2-digit',timeZone:TZ}).replace(/\s?(am|pm)$/i,function(x){return ' '+x.trim().toUpperCase();});}
  function hourLabel(h){return ((h%12)||12)+' '+(h>=12?'PM':'AM');}
  function hourShort(h){return ((h%12)||12)+(h>=12?'p':'a');}
  function dayLabel(iso){var d=new Date(iso+'T12:00:00+05:30');return d.toLocaleDateString('en-IN',{weekday:'short',day:'numeric',timeZone:TZ});}
  function km(m){return m>=1000?(m/1000).toFixed(m>=10000?0:1)+' km':Math.round(m/10)*10+' m';}
  function fresh(node){var n=node.cloneNode(true);node.replaceWith(n);return n;}
  function debounce(fn,ms){var t;return function(){var a=arguments,c=this;clearTimeout(t);t=setTimeout(function(){fn.apply(c,a);},ms);};}
  function store(k,v){try{if(v===undefined)return JSON.parse(localStorage.getItem(k)||'null');localStorage.setItem(k,JSON.stringify(v));}catch(e){return null;}}

  var KL={waterlogging:'Waterlogging',drain:'Blocked drain',heat:'Extreme heat spot',shade_water:'No shade or water',other:'Other issue'};
  var KIC={waterlogging:'drop',drain:'warning-circle',heat:'thermometer',shade_water:'umbrella-simple',other:'warning-circle'};
  var KCOL={waterlogging:'#5AB0FF',drain:'#FFC46B',heat:'#FF8A3D',shade_water:'#FF8A3D',other:'#B08A74'};
  var DL={ankle:'Ankle-deep',knee:'Knee-deep',waist:'Waist-deep',above_waist:'Above waist'};
  var STL={new:'Received',assigned:'Assigned',in_progress:'In progress',resolved:'Resolved',rejected:'Closed'};
  var STC={new:'new',assigned:'prog',in_progress:'prog',resolved:'ok',rejected:'un'};
  var SRC={sachet:'NDMA / IMD official',forecast:'Forecast, IMD thresholds',reports:'Citizen reports'};
  var PK={drinking_water:'Drinking water',shelter:'Shelter',community_centre:'Community centre',park:'Park',hospital:'Hospital'};
  var PIC={drinking_water:'drop',shelter:'buildings',community_centre:'buildings',park:'tree-evergreen',hospital:'buildings'};
  function isOpen(r){return r.status!=='resolved'&&r.status!=='rejected';}

  /* ward lookup on the same map geometry the site draws */
  var cctx=document.createElement('canvas').getContext('2d'),P2=WM.map(function(w){return new Path2D(w.d);});
  function wardAt(lat,lon){var xy=proj(lat,lon);for(var i=0;i<P2.length;i++)if(cctx.isPointInPath(P2[i],xy[0],xy[1]))return WM[i];return null;}
  function wardCenter(w){var ll=unproj(w.cx,w.cy);return {lat:ll[0],lon:ll[1]};}

  /* ---------- shared state ---------- */
  var S={wardId:U.PILOT.i,reports:[],alerts:[],feels:null,rain:null,summary:null,meta:null,attrs:null},hooks=[];
  var TREE=['#EEF5DC','#CFE6A8','#A8D27E','#7DBA5C','#559C45','#347C36','#1D5B29'];
  function treeOf(i){var a=S.attrs&&S.attrs[i];return a&&a.tree_frac!=null?a.tree_frac:null;}
  var savedW=store('us_ward');if(savedW!=null&&WM[savedW])S.wardId=savedW;
  function wname(){return WM[S.wardId].name;}
  function awsOn(f){return !!(S.meta&&S.meta.aws&&S.meta.aws[f]);}
  function verifiedTxt(r){return r.photo_verified===true?'Photo verified by Amazon Rekognition: water seen, so this report counts straight away':r.photo_verified===false?(r.needs_review?'We could not see water in this photo, so a ward officer will check it before it raises alerts or changes routes':'Photo checked: no water seen'):'';}
  function upsertReport(r){var i=-1;for(var k=0;k<S.reports.length;k++)if(S.reports[k].id===r.id){i=k;break;}
    var isNew=i<0;if(isNew)S.reports.unshift(r);else S.reports[i]=r;hooks.forEach(function(f){try{f(r,isNew);}catch(e){console.warn(e);}});return isNew;}

  /* ---------- header: ward picker, bell, clock ---------- */
  var sel=fresh($('#wardSel'));sel.value=S.wardId;
  function wardTexts(){var n=wname();$('#rpWard').textContent=n;$('#rpCenterT').textContent=n+' centre';$('#opsTitle').textContent=n+' operations';}
  function setWard(id,quiet){if(!WM[id])return;var changed=id!==S.wardId;S.wardId=id;sel.value=id;store('us_ward',id);wardTexts();locChip();
    if(changed&&window.__live)onWard();if(changed&&!quiet)toast('Showing '+wname());}
  sel.addEventListener('change',function(){setWard(+sel.value);});
  /* ---------- the user's own location ---------- */
  S.loc=null;var locWatch=null,locState='unknown';
  function userWard(){return S.loc?wardAt(S.loc.lat,S.loc.lon):null;}
  function atUser(){var w=userWard();return !!(w&&w.i===S.wardId);}  // is the shown ward the one the user is standing in?
  var chipB=document.createElement('button');chipB.type='button';chipB.className='loc-chip';chipB.id='locChip';sel.parentNode.insertBefore(chipB,sel);
  function locChip(){var w=userWard();
    if(w&&w.i===S.wardId){chipB.innerHTML=ic('map-pin')+'<span>You are here</span>';chipB.className='loc-chip on';chipB.title='Showing your location: '+w.name;}
    else if(w){chipB.innerHTML=ic('map-pin')+'<span>Back to my ward</span>';chipB.className='loc-chip';chipB.title='You are in '+w.name;}
    else{chipB.innerHTML=ic('crosshair')+'<span>'+(locState==='denied'?'Location off':'Use my location')+'</span>';chipB.className='loc-chip';chipB.title=locState==='denied'?'Location is blocked for this site in your browser settings':'Show heat and flood risk for where you are';}}
  chipB.addEventListener('click',function(){var w=userWard();if(w){setWard(w.i);return;}if(locState==='denied'){toast('Location is blocked. Allow it for this site in the browser, or pick a ward from the list');return;}locate(true);});
  function applyLoc(pos,announce){var prev=userWard();S.loc={lat:pos.coords.latitude,lon:pos.coords.longitude,acc:pos.coords.accuracy};var w=userWard();
    hideBanner();store('us_loc_ok',1);
    if(!w){locChip();if(announce)toast('You seem to be outside Delhi. Showing '+wname()+'; pick a ward to change it');return;}
    if(!prev||prev.i!==w.i){var follow=!prev||prev.i===S.wardId||announce;if(follow){setWard(w.i,true);toast(prev?'You are now in '+w.name:'Showing your area: '+w.name);}}
    locChip();if(!prev||prev.i!==w.i)pushSync();if(window.__mapYou)window.__mapYou(S.loc,!prev||prev.i!==w.i);if(inited.safe&&window.__safeLoad&&atUser()&&!prev)window.__safeLoad();}
  function locate(announce,done){done=done||function(){};if(!navigator.geolocation){toast('This browser cannot share location');done(false);return;}
    if(announce)chipB.classList.add('busy');
    navigator.geolocation.getCurrentPosition(function(p){chipB.classList.remove('busy');locState='granted';applyLoc(p,announce);watchLoc();done(true);},
      function(e){chipB.classList.remove('busy');if(e.code===1){locState='denied';hideBanner();}locChip();done(false);if(announce)toast(e.code===1?'Location blocked. Click the lock icon next to the address bar, allow Location, then try again':'Could not get your location: '+e.message);},
      {enableHighAccuracy:false,timeout:12000,maximumAge:300000});}
  function watchLoc(){if(locWatch!=null||!navigator.geolocation)return;  // riders move: follow them between wards while the app is open
    locWatch=navigator.geolocation.watchPosition(function(p){var m=S.loc?Math.hypot(p.coords.latitude-S.loc.lat,p.coords.longitude-S.loc.lon):1;if(m>.0015)applyLoc(p,false);},function(){},{enableHighAccuracy:false,maximumAge:120000,timeout:30000});}
  var banner=null;
  function showBanner(){if(banner||store('us_loc_ok')||store('us_loc_skip'))return;var top=$('.dash-top');if(!top)return;
    banner=document.createElement('div');banner.className='loc-banner';banner.setAttribute('role','region');banner.setAttribute('aria-label','Your location');
    banner.innerHTML=ic('map-pin')+'<div><b>See heat and flood risk where you are</b><span>Your location stays in this browser; it is only used to pick your ward and the nearest shade, water and routes.</span></div><button type="button" class="btn btn-primary sm" data-a="use">Use my location</button><button type="button" class="btn btn-ghost sm" data-a="pick">Pick a ward</button>';
    top.after(banner);banner.querySelector('[data-a=use]').onclick=function(){locate(true);};
    banner.querySelector('[data-a=pick]').onclick=function(){store('us_loc_skip',1);hideBanner();sel.focus();try{sel.showPicker&&sel.showPicker();}catch(e){}};
    if(M&&!reduce)M.animate(banner,{opacity:[0,1],y:[-8,0]},{duration:.4});}
  function hideBanner(){if(banner){banner.remove();banner=null;}}
  function startLocation(){locChip();
    if(navigator.permissions&&navigator.permissions.query){navigator.permissions.query({name:'geolocation'}).then(function(st){locState=st.state;
      if(st.state==='granted')locate(false);else if(st.state==='prompt')showBanner();locChip();
      st.onchange=function(){locState=st.state;if(st.state==='granted')locate(false);locChip();};}).catch(function(){showBanner();});}
    else showBanner();}
  fresh($('#bellBtn')).addEventListener('click',function(){openTab('dash');var n=S.alerts.length;toast(n?n+' active alert'+(n>1?'s':'')+' for Delhi':'No active alerts right now');});
  var clockEl=$('#clock');function tick(){clockEl.textContent=new Date().toLocaleString('en-IN',{weekday:'short',day:'numeric',month:'short',hour:'numeric',minute:'2-digit',timeZone:TZ}).replace(/\s?(am|pm)$/i,function(x){return ' '+x.trim().toUpperCase();});}
  setInterval(tick,15000);
  function onWard(){pushSync();loadHistory();youCard();if(inited.safe&&window.__safeLoad)window.__safeLoad();if(inited.fc)INIT.fc();if(inited.ops&&window.__opsLoad)window.__opsLoad();
    if(inited.report&&window.__pinWard)window.__pinWard();}

  /* ---------- dashboard ---------- */
  var LOC={'Narela':[28.853,77.093],'Burari':[28.753,77.199],'Model Town':[28.716,77.191],'Yamuna Vihar':[28.697,77.272],'Shahdara':[28.673,77.289],
    'Rohini':[28.736,77.113],'Pitampura':[28.699,77.132],'Civil Lines':[28.681,77.222],'Seelampur':[28.664,77.27],'Preet Vihar':[28.641,77.295],
    'Mundka':[28.683,77.031],'Rajouri Garden':[28.649,77.123],'Karol Bagh':[28.652,77.19],'Chandni Chowk':[28.656,77.23],'Laxmi Nagar':[28.631,77.277],
    'Mayur Vihar':[28.607,77.294],'Najafgarh':[28.609,76.98],'Janakpuri':[28.622,77.088],'Patel Nagar':[28.651,77.16],'Connaught Place':[28.632,77.218],
    'Kashmere Gate':[28.667,77.228],'Okhla':[28.539,77.282],'Dwarka':[28.582,77.05],'Uttam Nagar':[28.621,77.055],'Vasant Kunj':[28.52,77.158],
    'Lajpat Nagar':[28.567,77.243],'Defence Colony':[28.573,77.231],'Kalkaji':[28.54,77.259],'Mehrauli':[28.521,77.178],'Saket':[28.524,77.207],'Sangam Vihar':[28.496,77.244]};
  D.W.forEach(function(w){var ll=LOC[w[0]];var wd=ll&&wardAt(ll[0],ll[1]);if(!wd&&ll){var xy=proj(ll[0],ll[1]);wd=WM.reduce(function(a,b){return Math.hypot(b.cx-xy[0],b.cy-xy[1])<Math.hypot(a.cx-xy[0],a.cy-xy[1])?b:a;});}
    w._ward=wd?wd.i:null;w.label=w[0]+(wd&&wd.name.toLowerCase()!==w[0].toLowerCase()?' · '+wd.name:'');});
  function niceRange(vals,minSpan,floor0){var v=vals.filter(function(x){return x!=null;});if(!v.length)return [0,1];var lo=Math.floor(Math.min.apply(0,v)),hi=Math.ceil(Math.max.apply(0,v));
    if(floor0)lo=0;if(hi-lo<minSpan)hi=lo+minSpan;return [lo,hi];}
  function loadDash(){
    return Promise.all([get('/api/weather/city'),get('/api/layers/feels_like'),get('/api/layers/rain_6h')]).then(function(res){
      var sum=res[0];S.summary=sum;S.feels=res[1].values;S.rain=res[2].values;
      D.W.forEach(function(w){var id=w._ward;w[3]=id==null?null:S.feels[id].value;w[4]=id==null?null:S.rain[id].value;});
      D.RNG.heat=niceRange(S.feels.map(function(x){return x.value;}),4);D.RNG.flood=niceRange(S.rain.map(function(x){return x.value;}),5,true);
      D.paint();
      $('#mapTitle').textContent=D.getMode()==='heat'?'Feels-like temperature now':'Rain expected, next 6 hours';
      $('#mapSub').textContent='Open-Meteo, updated '+Math.max(1,Math.round((sum.age_s||0)/60))+' min ago';
      D.gauge(sum.feels_like_max==null?30:sum.feels_like_max);
      youCard();
      $('#rainKpi').textContent=(sum.rain_6h_max||0)+' mm';
      var nx=sum.next_24h||[];D.spark(nx.slice(0,12).map(function(h){return h.rain_mm||0;}));
      var bars=nx.slice(0,15).map(function(h){return [hourShort(+h.time.slice(11,13)),Math.round((h.rain_mm||0)*10)/10];});
      var mx=Math.max(1,Math.max.apply(0,bars.map(function(b){return b[1];})));var top=mx<=2?2:mx<=5?5:mx<=10?10:Math.ceil(mx/10)*10;
      D.barChart($('#rainChart'),bars,{max:top,ticks:[0,top/2,top],now:true,note:mx<0.1?'No rain expected in the next 15 hours':'City average, mm per hour'});
      $('#rainSub').textContent='mm, next 15 hours';
    });}
  function youCard(){if(!S.summary||!S.feels)return;var v=S.feels[S.wardId]&&S.feels[S.wardId].value;
    $('#gaugeS').textContent=(atUser()?'Your area, ':'')+wname()+': '+(v==null?'–':v.toFixed(1)+'°C')+' now. Hottest ward '+S.summary.feels_like_max+'°C';}
  function loadHistory(){$('#tempSub').textContent=wname()+', loading';
    return get('/api/history/unsafe-hours?days=30&ward_id='+S.wardId).then(function(h){
      var tot=h.days.reduce(function(a,d){return a+d.hours;},0);
      D.bk(h.days.map(function(d){return {date:new Date(d.date+'T12:00:00+05:30').toISOString(),peak:d.hours};}));
      $('#tempSub').textContent=wname()+', feels-like 42°C or more, '+tot+' h in total';
    }).catch(function(e){$('#tempSub').textContent='History unavailable: '+e.message;});}
  var feedEl=$('#feed');
  function feedLi(sev,label,title,sub,t){return '<span class="sev '+sev+'">'+label+'</span><div><b>'+esc(title)+'</b><small>'+esc(sub)+'</small></div><time>'+t+'</time>';}
  function alertLi(a){var d=parseT(a.created_at);return feedLi(a.severity,U.SEVL[a.severity]||'Info',a.title,[SRC[a.source]||a.source,a.area].filter(Boolean).join(' · '),d?hm(d):'');}
  function renderFeed(flashFirst){feedEl.innerHTML='';
    if(!S.alerts.length){var li=document.createElement('li');li.innerHTML=feedLi('ok','Clear','No active alerts','Checked every 10 min: NDMA SACHET, the forecast and citizen reports',hm(new Date()));feedEl.appendChild(li);return;}
    S.alerts.slice(0,5).forEach(function(a,i){var li=document.createElement('li');if(flashFirst&&i===0)li.className='fresh';li.innerHTML=alertLi(a);li.title=a.body||'';feedEl.appendChild(li);});}
  function alertKpis(){$('#alertKpi').textContent=S.alerts.length;var hr=S.alerts.filter(function(a){var d=parseT(a.created_at);return d&&Date.now()-d<3600e3;}).length;
    $('#alertDelta').textContent=S.alerts.length?(hr?'+'+hr+' in the last hour':'none new in the last hour'):'All clear right now';}
  function loadAlerts(){return get('/api/alerts?limit=30').then(function(r){S.alerts=r.alerts;renderFeed();alertKpis();$('#feedSrc').textContent='Live';});}
  function reportKpi(){var open=S.reports.filter(isOpen),sev=open.filter(function(r){return r.severity==='crit';}).length;
    $('#k4k').textContent='Open citizen reports';$('#k4v').textContent=open.length;$('#k4s').textContent=sev+' severe, last 24 hours';}
  function loadReports(){return get('/api/reports?hours=24&limit=300').then(function(r){S.reports=r.reports;reportKpi();});}
  hooks.push(function(r,isNew){reportKpi();if(isNew){var li=document.createElement('li');li.className='fresh';
    li.innerHTML=feedLi(r.severity,U.SEVL[r.severity],'New report: '+KL[r.kind]+(r.depth?', '+DL[r.depth].toLowerCase():''),(r.address||r.ward_name||'')+' · citizen',hm(new Date()));
    feedEl.insertBefore(li,feedEl.firstChild);if(feedEl.children.length>5)feedEl.removeChild(feedEl.lastElementChild);}});

  function stream(){if(!window.EventSource)return;var es=new EventSource(API+'/api/stream');
    es.addEventListener('report',function(e){upsertReport(JSON.parse(e.data));});
    es.addEventListener('alert',function(e){var a=JSON.parse(e.data);S.alerts=S.alerts.filter(function(x){return x.id!==a.id;});S.alerts.unshift(a);renderFeed(true);alertKpis();
      if(M&&!reduce)M.animate($('#alertKpi'),{scale:[1.25,1]},{type:'spring',stiffness:300,damping:12});});
    es.addEventListener('broadcast',function(e){var b=JSON.parse(e.data);toast('Alert: '+String(b.message).slice(0,70)+(b.message_hi?' / '+String(b.message_hi).slice(0,50):''));});
    es.onopen=function(){setChip('on','Live');};es.onerror=function(){setChip('off','Reconnecting');};}

  /* ---------- Live map ---------- */
  INIT.map=function(){
    var box=$('#liveBox'),card=$('#spotCard'),lg=$('#liveLegend'),layers={heat:true,tree:false,water:true,drain:false,cool:false};if(S.attrs)$('#treeLay').hidden=false;
    $('#waterSub').textContent='Citizen reports, last 24 h';$('#drainLab').textContent='Drains and canals';$('#drainSub').textContent='OpenStreetMap, with blocked-drain reports';
    $$('[data-layer=heat]')[0].parentNode.querySelector('small').textContent='Feels-like now, by ward';
    var map=DMap($('#liveMap'),box,{tip:$('#liveTip'),tipText:function(w){var v=S.feels&&S.feels[w.i].value,t=treeOf(w.i),parts=[];if(v!=null)parts.push(v.toFixed(1)+'°C feels-like now');if(t!=null)parts.push(Math.round(t*100)+'% tree cover');return parts.join(' · ');}});
    var wayG=el('g',{},map.lay),waysState=0,pins={water:[],heat:[],drain:[],cool:[]},repPin={},coolState=0;
    function loadWays(){if(waysState)return;waysState=1;get('/api/waterways?kinds=drain,canal').then(function(r){var html='';
      r.waterways.forEach(function(w){var d='M'+w.coords.map(function(c){var xy=proj(c[1],c[0]);return xy[0].toFixed(1)+','+xy[1].toFixed(1);}).join('L');
        html+='<path class="way" d="'+d+'" stroke-width="'+(w.kind==='canal'?2.2:1.3)+'"><title>'+esc(w.name||(w.kind==='canal'?'Canal':'Drain'))+'</title></path>';});
      wayG.innerHTML=html;}).catch(function(e){waysState=0;toast('Drains layer: '+e.message);});}
    function key(k){return k==='waterlogging'?'water':k==='drain'?'drain':'heat';}
    function dropPin(id){var p=repPin[id];if(!p)return;p.remove();Object.keys(pins).forEach(function(k){pins[k]=pins[k].filter(function(x){return x!==p;});});delete repPin[id];}
    function addReport(r,defer){dropPin(r.id);if(!isOpen(r))return;var k=key(r.kind);
      var p=map.addPin(r.lat,r.lon,KCOL[r.kind],KIC[r.kind],function(){showReport(r);},'',defer);p.style.display=layers[k]?'':'none';pins[k].push(p);repPin[r.id]=p;}
    function showReport(r){show({title:KL[r.kind]+(r.address?', '+r.address.split(',')[0]:''),sev:r.severity,
      meta:r.ref+' · '+(r.ward_name||'')+' · '+ago(r.created_at)+' · '+STL[r.status]+(r.assignee?' ('+r.assignee+')':''),
      desc:(r.depth?DL[r.depth]+'. ':'')+(r.description||'No description given.')+(verifiedTxt(r)?' '+verifiedTxt(r)+'.':''),lat:r.lat,lon:r.lon,photo:r.photo_url?API+r.photo_url:null});}
    function show(r){$('#spotTitle').textContent=r.title;var sv=$('#spotSev');sv.className='sev '+r.sev;sv.textContent=U.SEVL[r.sev]||'Info';$('#spotMeta').textContent=r.meta;$('#spotDesc').textContent=r.desc;
      var im=card.querySelector('img');if(r.photo){if(!im){im=document.createElement('img');im.alt='Photo sent with the report';card.insertBefore(im,$('#spotDesc'));}im.src=r.photo;}else if(im)im.remove();
      card.hidden=false;card._r=r;if(M)M.animate(card,{opacity:[0,1],y:[16,0]},{type:'spring',stiffness:300,damping:26});var xy=proj(r.lat,r.lon);map.focus(xy[0],xy[1],r.zoom||320);}
    function loadCool(){if(coolState)return;coolState=1;get('/api/places/city?kinds=drinking_water,shelter,community_centre').then(function(r){
      r.places.forEach(function(p){pins.cool.push(map.addPin(p.lat,p.lon,p.kind==='drinking_water'?'#8CC6FF':'#9FD3A8',PIC[p.kind],function(){showPlace(p);},'small',true));});
      map.set();apply();}).catch(function(e){coolState=0;toast('Shelters layer: '+e.message);});}
    function showPlace(p){var w=wardAt(p.lat,p.lon);show({title:p.name,sev:'info',meta:(PK[p.kind]||'Public point')+(p.opening_hours?' · '+p.opening_hours:'')+(w?' · '+w.name:''),
      desc:'Public point from OpenStreetMap. Opening hours and access can change, so check on the spot.',lat:p.lat,lon:p.lon,zoom:180});}
    function apply(){var r=D.RNG.heat;map.paths.forEach(function(p){var v=S.feels&&S.feels[p._w.i].value,t=treeOf(p._w.i);p.setAttribute('fill',layers.tree&&t!=null?pick(TREE,t/0.4):layers.heat&&v!=null?pick(HEAT,(v-r[0])/Math.max(1,r[1]-r[0])):'#3A1A10');});
      ['water','heat','drain','cool'].forEach(function(k){pins[k].forEach(function(p){p.style.display=layers[k]?'':'none';});});wayG.style.display=layers.drain?'':'none';
      var h=layers.tree?'<b>Tree cover (satellite)</b><i class="rampbar" style="background:linear-gradient(90deg,'+TREE.join(',')+')"></i><span class="rl"><span>0%</span><span>40%+</span></span>':layers.heat?'<b>Feels-like now</b><i class="rampbar" style="background:linear-gradient(90deg,'+HEAT.join(',')+')"></i><span class="rl"><span>'+r[0]+'°C</span><span>'+r[1]+'°C</span></span>':'';
      if(layers.water)h+='<span><i style="background:#5AB0FF"></i>Waterlogging report</span>';if(layers.heat)h+='<span><i style="background:#FF8A3D"></i>Heat or shade report</span>';
      if(layers.drain)h+='<span><i style="background:#3E86D8"></i>Drain or canal</span><span><i style="background:#FFC46B"></i>Blocked drain report</span>';
      if(layers.cool)h+='<span><i style="background:#9FD3A8"></i>Shelter</span><span><i style="background:#8CC6FF"></i>Drinking water</span>';lg.innerHTML=h||'<b>No layers on</b>';}
    $$('[data-layer]').forEach(function(c){c=fresh(c);c.addEventListener('change',function(){layers[c.dataset.layer]=c.checked;var other=c.dataset.layer==='tree'?'heat':c.dataset.layer==='heat'?'tree':null;if(other&&c.checked&&layers[other]){layers[other]=false;$$('[data-layer='+other+']')[0].checked=false;}if(c.checked&&c.dataset.layer==='drain')loadWays();if(c.checked&&c.dataset.layer==='cool')loadCool();apply();});});
    S.reports.forEach(function(r){addReport(r,true);});map.set();apply();
    var youPin=null;window.__mapYou=function(L,focus){if(youPin)youPin.remove();if(!L||!wardAt(L.lat,L.lon))return;
      youPin=map.addPin(L.lat,L.lon,'#4DA3F5','map-pin',function(){var w=wardAt(L.lat,L.lon);show({title:'You are here',sev:'info',meta:(w?w.name:'')+' · accuracy about '+Math.round(L.acc||0)+' m',desc:'Heat, shade, water and routes in the app use this spot. It stays in your browser.',lat:L.lat,lon:L.lon,zoom:160});},'you');
      if(focus){var xy=proj(L.lat,L.lon);map.focus(xy[0],xy[1],200);}};
    if(S.loc)setTimeout(function(){window.__mapYou(S.loc,true);},300);
    var list=$('#repList');function renderList(){var open=S.reports.filter(isOpen).slice(0,6);list.innerHTML=open.length?'':'<li class="empty">No open citizen reports in the last 24 hours.</li>';
      open.forEach(function(r){var li=document.createElement('li');li.innerHTML='<button type="button">'+ic(KIC[r.kind])+'<span><b>'+esc(KL[r.kind]+(r.address?', '+r.address.split(',')[0]:''))+'</b><small>'+esc((r.ward_name||'')+' · '+ago(r.created_at))+'</small></span>'+sevChip(r.severity)+'</button>';li.firstChild.onclick=function(){showReport(r);};list.appendChild(li);});}
    renderList();hooks.push(function(r){addReport(r);renderList();});
    window.__mapRefresh=function(){S.reports.forEach(function(r){addReport(r,true);});map.set();apply();renderList();};
    var inp=fresh($('#mapSearch')),sg=$('#mapSugg'),idx=-1,gseq=0;
    function items(res){sg.innerHTML='';idx=-1;if(!res.length){sg.innerHTML='<li>No match in Delhi</li>';sg.classList.add('on');return;}
      res.slice(0,8).forEach(function(r){var li=document.createElement('li');li.setAttribute('role','option');li.innerHTML='<span>'+esc(r.label)+'</span><small>'+r.kind+'</small>';li.onmousedown=function(e){e.preventDefault();r.go();inp.value=r.label;sg.classList.remove('on');};sg.appendChild(li);});sg.classList.add('on');}
    function wardHits(q){return WM.filter(function(w){return w.name.toLowerCase().indexOf(q)>=0;}).slice(0,5).map(function(w){return {label:w.name,kind:'Ward',go:function(){card.hidden=true;map.focus(w.cx,w.cy,150);flash(w);}};});}
    var geo=debounce(function(q,local){var my=++gseq;get('/api/geocode?q='+encodeURIComponent(q)).then(function(r){if(my!==gseq)return;
      items(local.concat(r.results.map(function(g){return {label:g.name.split(',').slice(0,3).join(','),kind:'Place',go:function(){var w=wardAt(g.lat,g.lon);show({title:g.name.split(',')[0],sev:'info',meta:(w?w.name:'Delhi')+' · from OpenStreetMap',desc:g.name,lat:g.lat,lon:g.lon,zoom:120});}};})));}).catch(function(){});},350);
    function search(){var q=inp.value.trim().toLowerCase();if(q.length<2){sg.classList.remove('on');return;}var local=wardHits(q);items(local);if(q.length>=3)geo(q,local);}
    function flash(w){var p=map.paths[w.i];p.classList.add('pilot');if(M)M.animate(p,{opacity:[.3,1,.5,1]},{duration:1.2});setTimeout(function(){p.classList.remove('pilot');},2600);}
    inp.addEventListener('input',search);inp.addEventListener('blur',function(){setTimeout(function(){sg.classList.remove('on');},150);});
    inp.addEventListener('keydown',function(e){var it=$$('li',sg);if(e.key==='ArrowDown')idx=Math.min(it.length-1,idx+1);else if(e.key==='ArrowUp')idx=Math.max(0,idx-1);else if(e.key==='Enter'&&it[Math.max(0,idx)]){e.preventDefault();if(it[Math.max(0,idx)].onmousedown)it[Math.max(0,idx)].onmousedown({preventDefault:function(){}});return;}else return;it.forEach(function(x,k){x.classList.toggle('act',k===idx);});});
    fresh($('#spotAvoid')).onclick=function(){var r=card._r;openTab('route',{after:function(){if(window.__routeTo)window.__routeTo(r);}});};
    fresh($('#spotAdd')).onclick=function(){var r=card._r;openTab('report',{after:function(){if(window.__pinTo)window.__pinTo(r.lat,r.lon);}});};
    window.__liveFocus=function(p){var c=$$('[data-layer=cool]')[0];if(!c.checked){c.checked=true;layers.cool=true;loadCool();apply();}showPlace(p);};
  };

  /* ---------- Simulate (server models) ---------- */
  INIT.sim=function(){
    $('#sLakeT').textContent='Add 20 mm local storage (ponds, pits)';
    var box=$('#simBox'),mode='flood',st={mm:80,dr:true,lake:false,temp:42,hour:15,green:0,roof:false},res=null,cmp=null;
    var DLB={dry:'dry',ankle:'ankle-deep',knee:'knee-deep',waist:'waist-deep',above_waist:'above waist'};
    var map=DMap($('#simMap'),box,{tip:$('#simTip'),tipText:function(w){if(!res||!res.byId)return '';var r=res.byId[w.i];if(!r)return '';
      return mode==='flood'?r.depth_m.toFixed(2)+' m, '+DLB[r.label]:r.feels_like.toFixed(1)+'°C feels-like';}});
    var imp=$('#impact'),chipEl=$('#simChip'),lg=$('#simLegend'),busy=false,again=false;
    function idx(rows){var o={};rows.forEach(function(r){o[r.ward_id]=r;});return o;}
    function fmtH(h){return hourLabel(h);}
    function cell(k,v){return '<div>'+k+'<b>'+v+'</b></div>';}
    function run(){if(busy){again=true;return;}busy=true;again=false;var p;
      if(mode==='flood'){var body={rain_mm:st.mm,hours:2,drains_clean:st.dr,extra_storage_mm:st.lake?20:0},other={rain_mm:st.mm,hours:2,drains_clean:!st.dr,extra_storage_mm:body.extra_storage_mm};
        p=Promise.all([post('/api/simulate/flood',body),post('/api/simulate/flood',other)]).then(function(a){if(mode!=='flood')return;res=a[0];res.byId=idx(res.wards);cmp=a[1];
          map.paths.forEach(function(pth){var r=res.byId[pth._w.i],d=r?r.depth_m:0;pth.setAttribute('fill',d<.05?'#3A1A10':pick(WATER,d/1.1));});
          var clean=st.dr?res:cmp,choked=st.dr?cmp:res,saved=choked.wards_wet-clean.wards_wet;
          imp.innerHTML=cell('Wards knee-deep or worse',res.wards_wet)+cell('Wards waist-deep',res.wards_unsafe)+cell('Underpasses unsafe',res.underpasses_unsafe)+cell(st.dr?'Wards spared by clean drains':'Wards clean drains would spare',saved);
          var u=res.underpasses[0];chipEl.textContent=u?u.name+' · '+u.depth_m.toFixed(1)+' m':'No underpass floods at '+st.mm+' mm';
          legendRamp(lg,'Water depth',WATER,'0 m','1.1 m+');});}
      else{var hb={air_temp_c:st.temp,hour:st.hour,extra_green_pct:st.green,cool_roofs:st.roof};
        p=Promise.all([post('/api/simulate/heat',hb),(st.green||st.roof)?post('/api/simulate/heat',{air_temp_c:st.temp,hour:st.hour,extra_green_pct:0,cool_roofs:false}):null]).then(function(a){if(mode!=='heat')return;res=a[0];res.byId=idx(res.wards);
          map.paths.forEach(function(pth){var r=res.byId[pth._w.i];pth.setAttribute('fill',r?pick(HEAT,(r.feels_like-36)/14):'#3A1A10');});
          var cool=a[1]?(a[1].mean-res.mean):0;
          imp.innerHTML=cell('Peak feels-like',res.max.toFixed(1)+'°C')+cell('Wards at 45°C or more',res.wards_above_45)+cell('City average',res.mean.toFixed(1)+'°C')+cell('Cooling from trees and roofs',(cool>0?'-':'')+cool.toFixed(1)+'°C');
          chipEl.textContent='Scenario: '+st.temp+'°C at '+fmtH(st.hour)+(st.green?' · +'+st.green+'% trees':'')+(st.roof?' · cool roofs':'');
          legendRamp(lg,'Feels-like',HEAT,'36°C','50°C+');});}
      p.catch(function(e){imp.innerHTML=cell('Simulation unavailable',esc(e.message));}).then(function(){busy=false;if(again)run();});}
    function bindR(id,key,fmt){var r=$('#'+id),o=$('#'+id+'O');function f(){st[key]=+r.value;paintRange(r);o.textContent=fmt(+r.value);run();}r.addEventListener('input',f);st[key]=+r.value;paintRange(r);o.textContent=fmt(+r.value);return {r:r,f:f};}
    $('#sHour').value=15;
    var rain=bindR('sRain','mm',function(v){return v+' mm';});bindR('sTemp','temp',function(v){return v+'°C';});var hour=bindR('sHour','hour',fmtH);bindR('sGreen','green',function(v){return v+'%';});
    $('#sDrain').onchange=function(e){st.dr=e.target.checked;run();};$('#sLake').onchange=function(e){st.lake=e.target.checked;run();};$('#sRoof').onchange=function(e){st.roof=e.target.checked;run();};
    var playB=$('#simPlay');
    segBind($('.seg2'),function(b){mode=b.dataset.sm;res=null;$$('.sim-ctrl').forEach(function(c){c.hidden=c.dataset.for!==mode;});playB.querySelector('span').textContent=mode==='flood'?'Play storm':'Play day';run();});
    var playing=null;playB.onclick=function(){if(playing){playing.pause();playing=null;return;}if(!A)return;var o={v:0},tgt=mode==='flood'?rain:hour;
      playing=A.animate(o,{v:1,duration:9000,ease:'linear',onUpdate:function(){var nv=mode==='flood'?Math.round(Math.sin(o.v*Math.PI)*160/5)*5:Math.round(6+o.v*16);if(+tgt.r.value!==nv){tgt.r.value=nv;tgt.f();}},onComplete:function(){playing=null;}});};
    $('#simSend').onclick=function(){var text=mode==='flood'?
      'Flood warning: heavy rain of about '+st.mm+' mm in 2 hours could leave '+(res?res.wards_wet:'several')+' wards knee-deep'+(res&&res.underpasses[0]?', including '+res.underpasses[0].name:'')+'. Avoid underpasses and low roads.':
      'Heat warning: feels-like up to '+(res?res.max.toFixed(0):st.temp)+'°C around '+fmtH(st.hour)+'. Outdoor workers: rest in shade and drink water every 20 minutes.';
      broadcastModal(text);};
    run();
  };

  /* ---------- Safe hours ---------- */
  INIT.safe=function(){
    var role='rider',RM={rider:'rider',build:'construction',vendor:'vendor'},CAT={safe:'Safe',caution:'Caution',unsafe:'Unsafe'},near=[];
    function uvw(u){return u==null?'':u<3?', low':u<6?', moderate':u<8?', high':u<11?', very high':', extreme';}
    function load(){$('#safeSub').textContent=wname()+', loading';
      var q=atUser()?'lat='+S.loc.lat.toFixed(5)+'&lon='+S.loc.lon.toFixed(5):'ward_id='+S.wardId;
      return Promise.all([get('/api/safe-hours?role='+RM[role]+'&'+q),get('/api/weather/ward?hours=1&'+q)]).then(function(a){render(a[0],a[1]);})
        .catch(function(e){$('#safeSub').textContent='Safe hours unavailable: '+e.message;});}
    function render(sh,wx){var lim=sh.limits_wbgt,now=sh.now,uw=sh.unsafe_window,n=wx.now||{};
      var tf=treeOf(S.wardId);$('#safeSub').textContent=(atUser()?'Where you are, ':'')+wname()+(tf!=null?' ('+Math.round(tf*100)+'% tree cover)':'')+' today, by hour, for '+sh.role_label.toLowerCase()+' work ('+sh.workload+' effort).';
      var strip=$('#hstrip');strip.innerHTML='';
      sh.hours.forEach(function(h){var d=document.createElement('div');d.className='hc '+h.category+(h.is_now?' now':'');
        d.title=hourLabel(h.hour)+': feels-like '+h.feels_like+'°C, heat stress (WBGT) '+h.wbgt+'°C, '+CAT[h.category].toLowerCase();
        d.innerHTML='<span>'+Math.round(h.feels_like)+'°</span><small>'+hourShort(h.hour)+'</small>';strip.appendChild(d);});
      var fl=n.apparent_temperature;
      $('#safeK').textContent=now?CAT[now.category]+' now · feels-like '+Math.round(now.feels_like)+'°C':'Outside working hours · feels-like '+(fl==null?'–':Math.round(fl))+'°C';
      $('#safeBig').textContent=sh.rest_until?'Rest until '+sh.rest_until:now&&now.category==='caution'?'Work with care':now?'Safe to work now':uw?'Unsafe '+uw[0].replace(':00','')+' to '+uw[1].replace(':00',''):'No unsafe hours today';
      $('#safeTxt').textContent=sh.advice||(uw?'Plan the hardest tasks before '+uw[0].replace(':00','')+' and after '+uw[1].replace(':00','')+'. Drink a cup of water every 20 minutes and rest in shade.':'Heat stays below your limit today. Keep water close anyway.');
      $('#hNote').textContent=(uw?'Unsafe from '+uw[0]+' to '+uw[1]+'. Best windows: before '+uw[0]+' and after '+uw[1]+'. ':'No unsafe hours today for this work. ')+'Limits for '+sh.workload+' work: caution from WBGT '+lim.action_limit+'°C, unsafe from '+lim.tlv+'°C (ACGIH).';
      $('#nowH').textContent='Right now, '+hm(new Date());
      $('#nowList').innerHTML=[['Temperature',n.temperature_2m==null?'–':n.temperature_2m+'°C'],['Feels-like',fl==null?'–':fl+'°C'],['Humidity',n.relative_humidity_2m==null?'–':Math.round(n.relative_humidity_2m)+'%'],
        ['UV index',n.uv_index==null?'–':n.uv_index+uvw(n.uv_index)],['Wind',n.wind_speed_10m==null?'–':Math.round(n.wind_speed_10m)+' km/h']].map(function(r){return '<div><dt>'+r[0]+'</dt><dd>'+r[1]+'</dd></div>';}).join('');
      near=sh.near||[];var row=$('#nearRow'),pickN=[];['drinking_water','park','shelter','community_centre'].forEach(function(k){var p=near.filter(function(x){return x.kind===k;})[0];if(p&&pickN.length<3)pickN.push(p);});
      near.forEach(function(p){if(pickN.length<3&&pickN.indexOf(p)<0)pickN.push(p);});
      row.innerHTML=pickN.length?'':'<p class="empty">No mapped water points, parks or shelters within 5 km of '+(atUser()?'you':'this ward centre')+'.</p>';
      pickN.forEach(function(p){var b=document.createElement('button');b.className='near-item';b.type='button';
        b.innerHTML=ic(PIC[p.kind]||'map-pin')+'<span><b>'+esc(p.name)+'</b><small>'+esc((PK[p.kind]||'')+', '+km(p.distance_m)+(p.opening_hours?' · '+p.opening_hours:''))+'</small></span>'+ic('arrow-right');
        b.onclick=function(){openTab('map',{after:function(){setTimeout(function(){window.__liveFocus&&window.__liveFocus(p);},60);}});};row.appendChild(b);});
      if(A&&!reduce)A.animate('#hstrip .hc',{opacity:[0,1],translateY:[10,0],duration:500,ease:'outExpo',delay:A.stagger(25)});}
    segBind($('.seg3'),function(b){role=b.dataset.role;S.role=RM[role];load();pushSync();});
    var timer=null,btn=fresh($('#remindBtn'));btn.onclick=function(){
      if(timer){clearInterval(timer);timer=null;btn.querySelector('span').textContent='Set break reminders';toast('Break reminders off');return;}
      function startR(n){timer=setInterval(function(){var msg='Rest 10 minutes in shade and drink a cup of water.';if(n)try{new Notification('Raहत break',{body:msg});}catch(e){}toast(msg);},60*60*1000);
        btn.querySelector('span').textContent='Reminders on';toast(n?'You will get a notification every hour while this page is open':'Reminders on. Keep this page open to get them');}
      if('Notification' in window&&Notification.permission!=='denied')Notification.requestPermission().then(function(p){startR(p==='granted');});else startR(false);};
    window.__safeLoad=load;load();pushSetup();
  };

  /* ---------- Report ---------- */
  var KMAP={'Waterlogged road':'waterlogging','Blocked drain':'drain','Extreme heat spot':'heat','No shade or water':'shade_water'};
  var DMAP={'Ankle':'ankle','Knee':'knee','Waist':'waist','Above waist':'above_waist'};
  INIT.report=function(){
    var box=$('#pinBox'),addr=$('#rpAddr span'),cur={lat:null,lon:null,ward:null,address:null},rseq=0,photo=null;
    var map=DMap($('#pinMap'),box,{onPanEnd:upd,onPan:function(){}});
    function upd(){var c=map.center(),ll=unproj(c[0],c[1]);cur.lat=ll[0];cur.lon=ll[1];var w=wardAt(cur.lat,cur.lon);cur.ward=w;cur.address=null;
      addr.textContent=w?'Near '+w.name:'Outside Delhi wards. Move the pin into Delhi.';var my=++rseq;
      if(w)get('/api/reverse?lat='+cur.lat.toFixed(6)+'&lon='+cur.lon.toFixed(6)).then(function(r){if(my!==rseq)return;cur.address=r.address;if(r.address)addr.textContent=r.address+' · '+w.name;}).catch(function(){});}
    function goTo(x,y,z){map.focus(x,y,z||70);setTimeout(upd,850);}
    window.__pinTo=function(lat,lon){var xy=proj(lat,lon);goTo(xy[0],xy[1],40);};
    window.__pinWard=function(){if(atUser()){window.__pinTo(S.loc.lat,S.loc.lon);return;}var w=WM[S.wardId];goTo(w.cx,w.cy);};
    requestAnimationFrame(window.__pinWard);$('#rpCenter').onclick=window.__pinWard;
    if(navigator.geolocation){var me=document.createElement('button');me.className='txt-btn';me.type='button';me.innerHTML=ic('map-pin')+'My location';$('#rpCenter').after(me);
      me.onclick=function(){navigator.geolocation.getCurrentPosition(function(p){if(!wardAt(p.coords.latitude,p.coords.longitude)){toast('You seem to be outside Delhi. Drag the map to the spot instead');return;}window.__pinTo(p.coords.latitude,p.coords.longitude);},function(e){toast('Location not available: '+e.message);},{enableHighAccuracy:true,timeout:10000});};}
    var form=$('#rpForm'),depth=$('#depthFs'),err=$('#typeErr'),file=$('#rPhoto'),prev=$('#rPrev'),dz=$('#dropZone'),dt=$('#dropTxt'),submit=form.querySelector('[type=submit]');
    $$('input[name=rtype]').forEach(function(r){r.addEventListener('change',function(){err.hidden=true;depth.hidden=r.value!=='Waterlogged road';});});
    function load(f){if(!f||!/^image\//.test(f.type)){if(f)toast('Choose a photo file');return;}if(f.size>8*1024*1024){toast('That photo is over 8 MB');return;}photo=f;var rd=new FileReader();rd.onload=function(){prev.src=rd.result;prev.hidden=false;dt.textContent=f.name;};rd.readAsDataURL(f);}
    file.onchange=function(){load(file.files[0]);};['dragenter','dragover'].forEach(function(t){dz.addEventListener(t,function(e){e.preventDefault();dz.classList.add('over');});});
    ['dragleave','drop'].forEach(function(t){dz.addEventListener(t,function(e){e.preventDefault();dz.classList.remove('over');if(t==='drop')load(e.dataTransfer.files[0]);});});
    var dr=store('us_draft');if(dr){var t=$('input[name=rtype][value="'+dr.type+'"]');if(t){t.checked=true;depth.hidden=dr.type!=='Waterlogged road';}var dd=$('input[name=depth][value="'+dr.depth+'"]');if(dd)dd.checked=true;$('#rDesc').value=dr.desc||'';}
    $('#rpDraft').onclick=function(){var t=$('input[name=rtype]:checked'),d=$('input[name=depth]:checked');store('us_draft',{type:t&&t.value,depth:d&&d.value,desc:$('#rDesc').value});toast('Draft saved in this browser');};
    var done=$('#rpDone');
    form.addEventListener('submit',function(e){e.preventDefault();var t=$('input[name=rtype]:checked');
      if(!t){err.hidden=false;if(M)M.animate($('.types'),{x:[0,-8,8,-4,4,0]},{duration:.4});return;}
      if(!cur.ward){toast('Move the pin inside Delhi first');return;}
      var fd=new FormData(),kind=KMAP[t.value];fd.append('kind',kind);fd.append('lat',cur.lat.toFixed(6));fd.append('lon',cur.lon.toFixed(6));
      if(kind==='waterlogging'){var d=$('input[name=depth]:checked');if(d)fd.append('depth',DMAP[d.value]);}
      var desc=$('#rDesc').value.trim();if(desc)fd.append('description',desc.slice(0,1000));if(photo)fd.append('photo',photo,photo.name||'photo.jpg');
      submit.disabled=true;var old=submit.textContent;submit.textContent='Sending';
      get('/api/reports',{method:'POST',body:fd,timeout:60000}).then(function(r){
        $('#rpRef').textContent=KL[r.kind]+' near '+(r.address||r.ward_name)+'. Reference '+r.ref+'.'+(verifiedTxt(r)?' '+verifiedTxt(r)+'.':'')+' Track it below; ward officers see it now.';done.hidden=false;
        if(M)M.animate(done,{opacity:[0,1]},{duration:.3});if(A&&!reduce){var c=$('.tick circle',done),p=$('.tick path',done);[c,p].forEach(function(x){var L=x.getTotalLength();x.style.strokeDasharray=L;x.style.strokeDashoffset=L;});A.animate(c,{strokeDashoffset:0,duration:700,ease:'outExpo'});A.animate(p,{strokeDashoffset:0,duration:500,delay:400,ease:'outExpo'});}
        var refs=store('us_refs')||[];refs.unshift(r.ref);store('us_refs',refs.slice(0,10));try{localStorage.removeItem('us_draft');}catch(x){}
        upsertReport(r);renderMine();
      }).catch(function(e){toast('Not sent: '+e.message);}).then(function(){submit.disabled=false;submit.textContent=old;});});
    $('#rpAgain').onclick=function(){form.reset();photo=null;prev.hidden=true;dt.innerHTML='Drag a photo here or <u>browse</u>';depth.hidden=false;done.hidden=true;};
    var mine=$('#rpMine');
    function renderMine(){var refs=store('us_refs')||[];if(!refs.length){mine.innerHTML='<li class="empty">Reports you send from this browser show up here with their status.</li>';return;}
      Promise.all(refs.slice(0,6).map(function(ref){return get('/api/reports/'+encodeURIComponent(ref)).catch(function(){return null;});})).then(function(rows){
        mine.innerHTML='';rows.filter(Boolean).forEach(function(r){var li=document.createElement('li');
          li.innerHTML='<span><b>'+esc(KL[r.kind])+' · '+esc(r.ref)+'</b><small>'+esc((r.address||r.ward_name||'').split(',')[0]+' · '+ago(r.created_at))+'</small></span><em class="st '+STC[r.status]+'">'+STL[r.status]+'</em>';mine.appendChild(li);});
        if(!mine.children.length)mine.innerHTML='<li class="empty">Your earlier reports are no longer on this server.</li>';});}
    renderMine();hooks.push(function(r){var refs=store('us_refs')||[];if(refs.indexOf(r.ref)>=0)renderMine();});
  };

  /* ---------- Safer route (OSRM + flood hazards) ---------- */
  INIT.route=function(){
    var old=$('#routeMap'),svg=document.createElementNS(NS,'svg');svg.setAttribute('class','dmap');svg.id='routeMap';svg.setAttribute('role','img');svg.setAttribute('aria-label','Map of Delhi with route options');old.replaceWith(svg);
    var box=$('#routeBox');box.insertAdjacentHTML('beforeend','<div class="map-zoom"><button class="icon-btn" data-z="in" aria-label="Zoom in">'+ic('plus')+'</button><button class="icon-btn" data-z="out" aria-label="Zoom out">'+ic('minus')+'</button><button class="icon-btn" data-z="fit" aria-label="Show all of Delhi">'+ic('crosshair')+'</button></div><div class="map-legend" id="rtLegend"><span><i style="background:#FFB23F"></i>Recommended</span><span><i style="background:#FF5A3C"></i>Passes flooding</span><span><i style="background:#B08A74"></i>Other option</span><span><i style="background:#5AB0FF"></i>Flood spot</span></div>');
    var map=DMap(svg,box,{}),rg=el('g',{},map.lay),eta=$('#eta'),ends={from:null,to:null},R=null,chosen=0,markers=[],hzPins=[];
    var fromI=$('#rtFrom'),toI=$('#rtTo');fromI.value='';toI.value='';fromI.placeholder='From: area, street or landmark';toI.placeholder='To: where are you going?';
    function setAlert(kind,k,t,p){var c=$('#rtAlert');c.style.background=kind==='calm'?'linear-gradient(160deg,#1E3A24,#12200F)':'';c.style.borderColor=kind==='calm'?'rgba(159,211,168,.3)':'';
      $('#rtAlertK').textContent=k;$('#rtAlertT').textContent=t;$('#rtAlertP').textContent=p;}
    function baseAlert(){var w=S.reports.filter(function(r){return isOpen(r)&&r.kind==='waterlogging';}).sort(function(a,b){var o={crit:0,warn:1,info:2};return o[a.severity]-o[b.severity];})[0];
      if(w)return setAlert('','Flood report · '+ago(w.created_at),(w.address||w.ward_name||'Waterlogged road').split(',')[0],(w.depth?DL[w.depth]:'Waterlogged')+' water reported by a citizen in '+w.ward_name+'. Routes below steer around it.');
      var a=S.alerts.filter(function(x){return /flood|rain/.test(x.kind||'');})[0];
      if(a)return setAlert('',(SRC[a.source]||'Alert')+' · '+U.SEVL[a.severity],a.title,a.body||a.area||'');
      setAlert('calm','No flooding reported now','Roads look clear','Routes are checked against citizen flood reports from the last 6 hours and underpasses the forecast says will flood.');}
    baseAlert();
    function ac(inp,list,key){var seq=0;var go=debounce(function(q){var my=++seq;var wards=WM.filter(function(w){return w.name.toLowerCase().indexOf(q.toLowerCase())>=0;}).slice(0,3).map(function(w){var c=wardCenter(w);return {name:w.name,lat:c.lat,lon:c.lon,kind:'Ward'};});
        render(wards);if(q.length<3)return;get('/api/geocode?q='+encodeURIComponent(q)).then(function(r){if(my!==seq)return;render(wards.concat(r.results.map(function(g){return {name:g.name.split(',').slice(0,2).join(','),lat:g.lat,lon:g.lon,kind:'Place'};})));}).catch(function(){});},300);
      function render(rs){list.innerHTML='';rs.slice(0,7).forEach(function(r){var li=document.createElement('li');li.innerHTML='<span>'+esc(r.name)+'</span><small>'+r.kind+'</small>';li.onmousedown=function(e){e.preventDefault();pickEnd(key,r);list.classList.remove('on');};list.appendChild(li);});list.classList.toggle('on',rs.length>0);}
      inp.addEventListener('input',function(){ends[key]=null;var q=inp.value.trim();if(q.length<2){list.classList.remove('on');return;}go(q);});
      inp.addEventListener('blur',function(){setTimeout(function(){list.classList.remove('on');},150);});
      inp.addEventListener('keydown',function(e){if(e.key==='Enter'){var f=list.querySelector('li');if(f&&f.onmousedown){e.preventDefault();f.onmousedown({preventDefault:function(){}});}}});}
    function pickEnd(key,r){ends[key]={lat:r.lat,lon:r.lon,name:r.name};(key==='from'?fromI:toI).value=r.name;drawEnds();if(ends.from&&ends.to)find();else(key==='from'?toI:fromI).focus();}
    ac(fromI,$('#rtFromS'),'from');ac(toI,$('#rtToS'),'to');
    if(S.loc&&userWard()){ends.from={lat:S.loc.lat,lon:S.loc.lon,name:'My location, '+userWard().name};fromI.value=ends.from.name;setTimeout(function(){drawEnds();},0);}
    var me=$('#rtMe');me.hidden=!navigator.geolocation;me.onclick=function(){navigator.geolocation.getCurrentPosition(function(p){var w=wardAt(p.coords.latitude,p.coords.longitude);
      if(!w){toast('You seem to be outside Delhi');return;}pickEnd('from',{lat:p.coords.latitude,lon:p.coords.longitude,name:'My location, '+w.name});},function(e){toast('Location not available: '+e.message);},{enableHighAccuracy:true,timeout:10000});};
    function drawEnds(){markers.forEach(function(m){m.remove();});markers=[];['from','to'].forEach(function(k){var e=ends[k];if(e)markers.push(map.addPin(e.lat,e.lon,'#FFF2E4',k==='from'?'house':'flag',null,'small'));});}
    function pathD(coords){return 'M'+coords.map(function(c){var xy=proj(c[1],c[0]);return xy[0].toFixed(1)+','+xy[1].toFixed(1);}).join('L');}
    function fit(coords){var xs=[],ys=[];coords.forEach(function(c){var xy=proj(c[1],c[0]);xs.push(xy[0]);ys.push(xy[1]);});
      var x0=Math.min.apply(0,xs),x1=Math.max.apply(0,xs),y0=Math.min.apply(0,ys),y1=Math.max.apply(0,ys),ar=svg.clientWidth/Math.max(1,svg.clientHeight);
      map.focus((x0+x1)/2,(y0+y1)/2,Math.max(60,Math.max(x1-x0,(y1-y0)*ar)*1.35));}
    function draw(){rg.innerHTML='';hzPins.forEach(function(p){p.remove();});hzPins=[];if(!R)return;var rec=R.alternatives.indexOf(R.alternatives.filter(function(a){return a.risk===R.recommended.risk&&a.duration_s===R.recommended.duration_s;})[0]);
      R.alternatives.forEach(function(a,i){if(i===chosen)return;el('path',{d:pathD(a.coords),'class':'route',stroke:a.risk>0?'#FF5A3C':'#B08A74','stroke-width':4,'stroke-dasharray':a.risk>0?'8 7':'',opacity:.75},rg);});
      var c=R.alternatives[chosen];var main=el('path',{d:pathD(c.coords),'class':'route',stroke:c.risk>0?'#FF5A3C':'#FFB23F','stroke-width':7},rg);R._main=main;
      el('path',{d:pathD(c.coords),'class':'route',stroke:'#FFF2E4','stroke-width':2,'stroke-dasharray':'2 18',opacity:.8},rg);
      if(A&&!reduce){var L=main.getTotalLength();main.style.strokeDasharray=L;main.style.strokeDashoffset=L;A.animate(main,{strokeDashoffset:0,duration:1200,ease:'inOutQuad',onComplete:function(){main.style.strokeDasharray='';}});}
      var seen={};R.alternatives.forEach(function(a){a.hazards.forEach(function(h){var k=h.lat+','+h.lon;if(seen[k])return;seen[k]=1;
        hzPins.push(map.addPin(h.lat,h.lon,'#5AB0FF','drop',function(){toast(h.name+': '+h.detail);},'',true));});});map.set();return rec;}
    function find(){var opts=$('#rtOpts');opts.innerHTML='<b class="side-h">Route options</b><p class="empty">Finding routes and checking them for flooding</p>';
      get('/api/route?from='+ends.from.lat.toFixed(6)+','+ends.from.lon.toFixed(6)+'&to='+ends.to.lat.toFixed(6)+','+ends.to.lon.toFixed(6),{timeout:40000}).then(function(r){R=r;
        chosen=r.alternatives.indexOf(r.alternatives.filter(function(a){return a.duration_s===r.recommended.duration_s&&a.distance_m===r.recommended.distance_m;})[0]);if(chosen<0)chosen=0;
        var fastI=r.alternatives.indexOf(r.alternatives.filter(function(a){return a.duration_s===r.fastest.duration_s&&a.distance_m===r.fastest.distance_m;})[0]),recI=chosen;
        var h='<b class="side-h">Route options</b>';r.alternatives.forEach(function(a,i){var name=a.avoids_hazards?'Route around all flooding':i===recI?(a.risk>0?'Least flooded route':'Safer route'):i===fastI?'Fastest route':'Other route';
          var via=a.via.filter(Boolean).slice(0,2).join(', ');var hz=a.hazards.length;
          h+='<label class="route-opt"><input type="radio" name="route" value="'+i+'"'+(i===chosen?' checked':'')+'><span><b>'+name+'</b><small>'+(via?'via '+esc(via)+' · ':'')+km(a.distance_m)+(hz?' · passes '+hz+' flood spot'+(hz>1?'s':''):', no flooding reported')+'</small></span>'+(hz?'<em class="blk">'+Math.round(a.duration_s/60)+' min</em>':'<em>'+Math.round(a.duration_s/60)+' min</em>')+'</label>';});
        if(r.provider)h+='<small class="empty" style="padding:0">Routes: '+esc(r.provider)+'</small>';opts.innerHTML=h;$$('input[name=route]',opts).forEach(function(x){x.onchange=function(){chosen=+x.value;draw();var a=R.alternatives[chosen];if(a.hazards.length)toast('This route passes '+a.hazards.length+' flood spot'+(a.hazards.length>1?'s':''));};});
        var all=[],seen={};r.alternatives.forEach(function(a){a.hazards.forEach(function(z){if(!seen[z.name]){seen[z.name]=1;all.push(z);}});});
        $('#rtAvoid').innerHTML=all.length?all.slice(0,5).map(function(z){return '<li>'+ic('warning-circle')+'Avoid '+esc(z.name.split(',')[0])+', '+esc(z.detail)+'</li>';}).join(''):'<li>'+ic('check')+'No reported or expected flooding on these routes right now.</li>';
        if(all.length){var z=all[0];setAlert('',z.type==='report'?'Flood report on your way':'Forecast flooding on your way',z.name.split(',')[0],'Water '+z.detail+'. '+(r.recommended.hazards.length?'Every route passes near it, so take extra care.':'The recommended route avoids it.'));}else baseAlert();
        draw();fit(r.recommended.coords);if(r.note)toast(r.note);
      }).catch(function(e){opts.innerHTML='<b class="side-h">Route options</b><p class="empty">'+esc(e.message)+'</p>';});}
    var run=null;fresh($('#routeGo')).onclick=function(){if(!R||!R._main){toast('Pick where you start and where you are going');fromI.focus();return;}
      var a=R.alternatives[chosen],path=R._main,L=path.getTotalLength(),t0=performance.now(),Dur=reduce?10:7000,dot=el('circle',{r:Math.max(2.5,map.vb.w/110),fill:'#FFB23F',stroke:'#170806','stroke-width':1.5,'vector-effect':'non-scaling-stroke'},rg);
      eta.hidden=false;if(run)cancelAnimationFrame(run);var mins=Math.round(a.duration_s/60),via=(a.via.filter(Boolean)[0]||'your route');
      (function step(now){var t=Math.min(1,(now-t0)/Dur),pt=path.getPointAtLength(L*t);dot.setAttribute('cx',pt.x);dot.setAttribute('cy',pt.y);
        eta.innerHTML='ETA '+Math.max(0,Math.round(mins*(1-t)))+' min<br><small>'+esc(t<1?'On '+via:'Arrived, '+(ends.to?ends.to.name:''))+'</small>';if(t<1)run=requestAnimationFrame(step);else setTimeout(function(){dot.remove();},1500);})(t0);};
    fresh($('#routeShare')).onclick=function(){if(!R){toast('Plan a route first');return;}var a=R.alternatives[chosen],via=a.via.filter(Boolean).slice(0,2).join(', ');
      var hz=[];R.alternatives.forEach(function(x){x.hazards.forEach(function(z){if(hz.indexOf(z.name.split(',')[0])<0)hz.push(z.name.split(',')[0]);});});
      var txt='Raहत route from '+ends.from.name+' to '+ends.to.name+': '+(via?'via '+via+', ':'')+Math.round(a.duration_s/60)+' min.'+(hz.length?' Flooding reported at '+hz.slice(0,3).join(', ')+'.':' No flooding reported on the way.');
      if(navigator.share)navigator.share({text:txt}).catch(function(){});else if(navigator.clipboard)navigator.clipboard.writeText(txt).then(function(){toast('Copied, paste it anywhere');},function(){toast(txt);});else toast(txt);};
    window.__routeSet=function(a,b){pickEnd('from',a);pickEnd('to',b);};
    window.__routeTo=function(r){if(r&&r.lat){setAlert('',r.sev==='info'?'Selected place':'Avoid this spot',r.title,(r.desc||'')+' Routes below are checked against it.');}fromI.focus();};
  };

  /* ---------- Ward officer tools ---------- */
  function broadcastModal(text){needToken(function(){var ch=(S.meta&&S.meta.channels)||{app:true};
    var m=modal('Broadcast an alert','<label class="sr" for="bcMsg">Message</label><textarea id="bcMsg">'+esc(text||'')+'</textarea>'+
      '<div class="chips" id="bcAud"><label class="chip"><input type="checkbox" value="residents" checked>Residents</label><label class="chip"><input type="checkbox" value="commuters" checked>Commuters</label><label class="chip"><input type="checkbox" value="outdoor_workers">Outdoor workers</label></div>'+
      '<div class="chips" id="bcScope"><label class="chip"><input type="radio" name="bcs" value="ward" checked>'+esc(wname())+'</label><label class="chip"><input type="radio" name="bcs" value="all">All of Delhi</label></div>'+
      '<div class="chips" id="bcCh"><label class="chip"><input type="checkbox" value="app" checked>In the app</label>'+(ch.sms?'<label class="chip"><input type="checkbox" value="sms">SMS</label>':'')+(ch.whatsapp?'<label class="chip"><input type="checkbox" value="whatsapp">WhatsApp</label>':'')+'</div>'+
      (awsOn('translate')?'<label class="chip" style="align-self:flex-start"><input type="checkbox" id="bcHi" checked>Also in Hindi (Amazon Translate)</label><p id="bcHiTxt" class="p-sub" style="font-size:13px"></p>':'')+
      (ch.sms||ch.whatsapp?'':'<p class="p-sub" style="font-size:12px">SMS and WhatsApp switch on once Twilio keys are in the server settings.</p>'),
      [{label:'Cancel'}].concat(awsOn('bedrock')?[{label:'Draft with AI',fn:function(m){var b=$$('.modal-act button',m).filter(function(x){return x.textContent==='Draft with AI';})[0];b.disabled=true;b.textContent='Drafting';
        off('/api/ai/draft-alert',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ward_id:S.wardId})}).then(function(d){m.querySelector('#bcMsg').value=d.en;var h=m.querySelector('#bcHiTxt');if(h&&d.hi)h.textContent='Hindi: '+d.hi;toast('Draft written by Amazon Bedrock from live data. Check it before sending');})
        .catch(function(e){toast('AI draft failed: '+e.message);}).then(function(){b.disabled=false;b.textContent='Draft with AI';});return false;}}]:[]).concat([{label:'Send alert',primary:true,fn:function(m){var msg=m.querySelector('#bcMsg').value.trim();if(msg.length<5){toast('Write a message first');return false;}
        var aud=$$('#bcAud input:checked',m).map(function(x){return x.value;}),chs=$$('#bcCh input:checked',m).map(function(x){return x.value;}),all=m.querySelector('input[name=bcs]:checked').value==='all';
        if(!aud.length||!chs.length){toast('Pick at least one audience and channel');return false;}
        var hiBox=m.querySelector('#bcHi');off('/api/broadcasts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,ward_id:all?null:S.wardId,audiences:aud,channels:chs,hindi:hiBox?hiBox.checked:false})})
          .then(function(r){toast('Alert sent'+(r.message_hi?' in English and Hindi':'')+(r.delivered?', '+r.delivered+' text messages delivered':'')+(r.failed?', '+r.failed+' failed':''));if(window.__opsLoad)window.__opsLoad();})
          .catch(function(e){toast('Not sent: '+e.message);});}}]));});}
  function needToken(then){if(TOK.get())return then();
    var m=modal('Ward officer sign-in','<p class="p-sub">Paste the OFFICER_TOKEN from the server settings (.env). It is kept in this browser only.</p><label class="sr" for="tokIn">Officer token</label><input id="tokIn" class="tok-in" type="password" autocomplete="off" spellcheck="false">',
      [{label:'Cancel'},{label:'Sign in',primary:true,fn:function(m){var v=m.querySelector('#tokIn').value.trim();if(!v){toast('Paste the token first');return false;}TOK.set(v);
        off('/api/ops/summary').then(function(){toast('Signed in as ward officer');then();}).catch(function(e){TOK.clear();toast(e.status===401?'That token is not right':'Sign-in failed: '+e.message);});}}]);
    setTimeout(function(){var i=m.querySelector('#tokIn');if(i){i.focus();i.addEventListener('keydown',function(e){if(e.key==='Enter')m.querySelector('.btn-primary').click();});}},50);}

  INIT.ops=function(){
    var filter='all',rows=[],body=$('#qBody'),scopeWard=false,hdr=$('[data-p=ops] .panel-h > div');
    hdr.insertAdjacentHTML('beforeend','<label class="switch" style="margin-top:8px;max-width:320px"><span id="opsScopeT">Only this ward</span><input type="checkbox" id="opsScope"></label>');
    $('#opsScope').onchange=function(e){scopeWard=e.target.checked;load();};
    function wq(){return scopeWard?'ward_id='+S.wardId:'';}
    function signedOut(){['opsOpen','opsFix','opsDrains','opsSent'].forEach(function(id){$('#'+id).textContent='–';});$('#opsOpenS').textContent='sign in to see';$('#opsFixS').textContent='';$('#opsSentS').textContent='';
      $('#opsSub').textContent='Officer tools need the token from the server settings.';$('#hotList').innerHTML='';
      body.innerHTML='<tr><td colspan="6" style="font-family:var(--sans)"><button class="btn btn-primary sm" type="button" id="opsSign">'+ic('lock-simple')+'<span>Sign in as ward officer</span></button></td></tr>';
      $('#opsSign').onclick=function(){needToken(load);};}
    function load(){$('#opsTitle').textContent=scopeWard?wname()+' operations':'Delhi ward operations';$('#opsScopeT').textContent='Only '+wname();
      if(!TOK.get())return signedOut();var q=wq();
      return Promise.all([off('/api/ops/summary'+(q?'?'+q:'')),get('/api/reports?hours=720&limit=500'+(q?'&'+q:''))]).then(function(a){var s=a[0];rows=a[1].reports;
        $('#opsOpen').textContent=s.open;$('#opsOpenS').textContent=s.open_severe+' severe, '+s.unassigned+' unassigned';
        $('#opsFix').textContent=s.avg_fix_hours==null?'–':s.avg_fix_hours+' h';
        $('#opsFixS').textContent=s.avg_fix_hours==null?'nothing resolved this week yet':s.avg_fix_hours_prev_week==null?'measured on '+s.resolved_this_week+' resolved this week':(Math.abs(s.avg_fix_hours_prev_week-s.avg_fix_hours).toFixed(1)+' h '+(s.avg_fix_hours<=s.avg_fix_hours_prev_week?'faster':'slower')+' than last week');
        $('#opsDrains').textContent=s.drains_cleared_this_week;$('#opsSent').textContent=s.broadcasts_this_week;$('#opsSentS').textContent=s.messages_delivered_this_week+' text messages delivered';
        $('#opsSub').textContent=s.open+' open report'+(s.open===1?'':'s')+', synced '+hm(new Date())+'.';
        var mx=Math.max(1,Math.max.apply(0,s.hotspots.map(function(h){return h.reports;})));
        $('#hotList').innerHTML=s.hotspots.length?s.hotspots.map(function(h){return '<li><span class="hl">'+esc(h.place.split(',')[0])+'<span>'+h.reports+' report'+(h.reports>1?'s':'')+'</span></span><i style="width:'+(h.reports/mx*100)+'%"></i></li>';}).join(''):'<li class="empty">No reports this week.</li>';
        render();}).catch(function(e){if(e.status===401)signedOut();else body.innerHTML='<tr><td colspan="6" style="font-family:var(--sans)">'+esc(e.message)+'</td></tr>';});}
    function stCell(r){if(isOpen(r)&&!r.assignee)return '<button class="assign" type="button">Assign crew</button>';
      return '<select class="st-sel" aria-label="Status">'+['assigned','in_progress','resolved','rejected'].concat(r.status==='new'?['new']:[]).map(function(k){return '<option value="'+k+'"'+(k===r.status?' selected':'')+'>'+STL[k]+(k===r.status&&r.assignee?' · '+esc(r.assignee):'')+'</option>';}).join('')+'</select>';}
    function patch(r,data){return off('/api/reports/'+r.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(x){upsertReport(x);toast(x.ref+': '+STL[x.status]+(x.assignee?', '+x.assignee:''));load();}).catch(function(e){toast('Not saved: '+e.message);});}
    function render(){var list=rows.filter(function(r){return filter==='all'?r.status!=='rejected':filter==='sev'?(r.severity==='crit'&&isOpen(r)):(isOpen(r)&&!r.assignee);});
      body.innerHTML='';list.slice(0,60).forEach(function(r){var tr=document.createElement('tr');
        tr.innerHTML='<td>'+esc(r.ref.replace(/^US-\d{4}-/,'#'))+'</td><td>'+esc(KL[r.kind]+(r.depth?', '+DL[r.depth].toLowerCase():''))+(r.photo_url?' <a href="'+API+r.photo_url+'" target="_blank" rel="noopener" style="color:var(--sun)">photo'+(r.photo_verified?' ✓ water':'')+'</a>':'')+(r.needs_review?' <span title="Amazon Rekognition saw no water in the photo" style="color:#FF9A7A">· check photo</span>':'')+'</td><td>'+esc((r.address||r.ward_name||'').split(',')[0])+'<br><small style="color:var(--dim)">'+esc(r.ward_name||'')+'</small>'+'</td><td>'+sevChip(r.severity)+'</td><td>'+ago(r.created_at)+'</td><td>'+stCell(r)+'</td>';
        var b=tr.querySelector('.assign');if(b)b.onclick=function(){var m=modal('Assign '+r.ref,'<label class="sr" for="crewIn">Crew</label><input id="crewIn" class="tok-in" value="Crew A" style="font-family:var(--sans)">',[{label:'Cancel'},{label:'Assign',primary:true,fn:function(m){var v=m.querySelector('#crewIn').value.trim();if(!v)return false;patch(r,{assignee:v});}}]);};
        var s=tr.querySelector('.st-sel');if(s)s.onchange=function(){patch(r,{status:s.value});};body.appendChild(tr);});
      if(!body.children.length)body.innerHTML='<tr><td colspan="6" style="color:var(--dim);font-family:var(--sans)">'+(rows.length?'Nothing here. Every report has a crew.':'No citizen reports in the last 30 days.')+'</td></tr>';}
    segBind($('.seg4'),function(b){filter=b.dataset.f;render();});
    fresh($('#csvBtn')).onclick=function(){needToken(function(){off('/api/ops/export.csv').then(function(csv){var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download='rahat-reports.csv';document.body.appendChild(a);a.click();a.remove();toast('CSV downloaded');}).catch(function(e){toast('Export failed: '+e.message);});});};
    fresh($('#bcBtn')).onclick=function(){var a=S.alerts[0];broadcastModal(a?a.title+'. '+(a.body||'').slice(0,240):'');};
    var ins=$('#opsInsight');ins.querySelector('b').textContent='Drain plan';
    get('/api/forecast?ward_id='+S.wardId).then(function(f){var wet=f.days.slice().sort(function(a,b){return b.rain_mm-a.rain_mm;})[0];var heavy=wet&&wet.rain_mm>=15;
      return post('/api/simulate/flood',{rain_mm:heavy?Math.max(wet.rain_mm,40):80,hours:heavy?6:2,drains_clean:false}).then(function(sim){
        var top=sim.wards.slice().sort(function(a,b){return b.depth_m-a.depth_m;}).slice(0,3).map(function(r){return WM[r.ward_id].name;}).join(', ');
        ins.querySelector('p').textContent=heavy?dayLabel(wet.date)+': '+wet.rain_mm+' mm forecast. With choked drains, '+top+' flood first. Clear their drains before then.':'No heavy rain in the 7-day forecast. In an 80 mm storm with choked drains, '+top+' flood first, so this is a good week to clear their drains.';});
    }).catch(function(e){ins.querySelector('p').textContent='Plan unavailable: '+e.message;});
    if(awsOn('bedrock')&&!ins.querySelector('.ai-btn')){var ab=document.createElement('button');ab.type='button';ab.className='txt-btn ai-btn';ab.style.marginTop='8px';ab.textContent='Ask AI for a 72-hour briefing';ins.appendChild(ab);
      ab.onclick=function(){needToken(function(){ab.disabled=true;ab.textContent='Thinking';off('/api/ai/ops-brief?ward_id='+S.wardId).then(function(r){ins.querySelector('b').textContent='AI briefing, '+wname();ins.querySelector('p').textContent=r.text;ab.textContent='Written by Amazon Bedrock from live data. Ask again';})
        .catch(function(e){toast('AI briefing failed: '+e.message);ab.textContent='Ask AI for a 72-hour briefing';}).then(function(){ab.disabled=false;});});};}
    hooks.push(debounce(function(){if(TOK.get())load();},800));window.__opsLoad=load;load();
  };

  /* ---------- Forecast ---------- */
  INIT.fc=function(){$('#fcTitle').textContent='Next 7 days in '+wname();$('#fcSub').textContent='Open-Meteo forecast. Risk labels use IMD heat-wave and heavy-rain thresholds.';
    return get('/api/forecast?ward_id='+S.wardId).then(function(r){
      $('#fcRow').innerHTML=r.days.map(function(d){var cls=d.risk==='heat'?'hot':d.risk==='flood'?'wet':'';var icn=d.rain_mm>=5?'cloud-rain':d.risk==='heat'?'sun':(d.rain_chance||0)>=40?'cloud-sun':'sun';
        var chipH=d.risk==='heat'?'<span class="sev crit">Heat risk</span>':d.risk==='flood'?'<span class="sev info">Flood risk</span>':d.risk==='moderate'?'<span class="sev warn">Moderate</span>':'<span class="sev ok">Low risk</span>';
        return '<div class="fc-day '+cls+'"><b>'+dayLabel(d.date)+'</b>'+ic(icn)+'<span class="fc-t">'+Math.round(d.temp_max)+'°</span><small>Feels-like '+Math.round(d.feels_max)+'°C</small><small>Rain '+(Math.round(d.rain_mm*10)/10)+' mm, '+(d.rain_chance==null?'–':d.rain_chance+'%')+'</small>'+chipH+'</div>';}).join('');
      var heat=r.days.filter(function(d){return d.risk==='heat';}).map(function(d){return dayLabel(d.date);}),wet=r.days.filter(function(d){return d.risk==='flood'||d.rain_mm>=15;}).map(function(d){return dayLabel(d.date);});
      var parts=[];if(wet.length)parts.push('Rain of 15 mm or more on '+wet.join(', ')+': clear drains and check pumps the day before.');
      if(heat.length)parts.push('Heat risk on '+heat.join(', ')+': open cooling centres and water points from 12 to 5 PM.');
      $('#fcNote').innerHTML='<b>Plan for the week:</b> '+esc(parts.length?parts.join(' '):'No heat or heavy-rain days ahead. Normal operations; a good week for drain cleaning.');
      if(M&&!reduce)$$('.fc-day').forEach(function(d,i){M.animate(d,{opacity:[0,1],y:[24,0]},{type:'spring',stiffness:220,damping:22,delay:i*.06});});
    }).catch(function(e){$('#fcRow').innerHTML='<p class="empty">'+esc(e.message)+'</p>';});};


  /* ---------- Push alerts (Web Push, works with the browser closed) ---------- */
  var PUSH_OK=('serviceWorker' in navigator)&&('PushManager' in window)&&window.isSecureContext&&API===location.origin;
  var IOS=/iphone|ipad|ipod/i.test(navigator.userAgent),STANDALONE=matchMedia('(display-mode: standalone)').matches||navigator.standalone;
  function b64key(s){var p='='.repeat((4-s.length%4)%4),b=atob((s+p).replace(/-/g,'+').replace(/_/g,'/')),a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a;}
  function swReg(){return navigator.serviceWorker.register('sw.js',{scope:'./'}).then(function(){return navigator.serviceWorker.ready;});}
  function pushBody(sub){var b={subscription:sub.toJSON(),ward_id:S.wardId,role:S.role||'rider',lang:store('us_lang')||'en'};
    if(S.loc&&userWard()){b.lat=S.loc.lat;b.lon=S.loc.lon;}return b;}
  function curSub(){if(!PUSH_OK)return Promise.resolve(null);return navigator.serviceWorker.getRegistration('./').then(function(r){return r?r.pushManager.getSubscription():null;});}
  var pushSync=debounce(function(){if(!store('us_push'))return;curSub().then(function(sub){if(sub)post('/api/push/subscribe',pushBody(sub)).catch(function(){});});},1500);
  function pushOn(){if(IOS&&!STANDALONE){modal('Alerts on iPhone','<p>On iPhone, alerts work once Raहत is on your home screen. Tap the Share button in Safari, choose <b>Add to Home Screen</b>, open Raहत from there and turn alerts on again.</p>',[{label:'OK',primary:true}]);return Promise.reject(new Error('ios'));}
    return Notification.requestPermission().then(function(p){if(p!=='granted')throw new Error(p==='denied'?'Notifications are blocked for this site. Allow them in the browser settings.':'Notifications not allowed');
      return Promise.all([swReg(),get('/api/push/key')]);}).then(function(a){var reg=a[0];
      return reg.pushManager.getSubscription().then(function(old){return old||reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64key(a[1].public_key)});});})
      .then(function(sub){return post('/api/push/subscribe',pushBody(sub)).then(function(){store('us_push',true);return sub;});});}
  function pushOff(){return curSub().then(function(sub){store('us_push',false);if(!sub)return;var ep=sub.endpoint;return sub.unsubscribe().then(function(){return get('/api/push/subscribe',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint:ep})});});});}
  function pushTest(){return curSub().then(function(sub){if(!sub)throw new Error('Alerts are off');return post('/api/push/test',{endpoint:sub.endpoint});});}
  function pushSetup(){var rb=$('#remindBtn');if(!rb||$('#pushRow'))return;
    var row=document.createElement('div');row.className='push-row';row.id='pushRow';rb.parentNode.insertBefore(row,rb);rb.remove();
    function draw(on){if(!PUSH_OK){row.innerHTML='<small>Phone alerts need the app opened from its own address over HTTPS.</small>';return;}
      row.innerHTML=on?'<button class="btn btn-ghost sm" type="button" data-a="test">'+ic('bell-ringing')+'<span>Alerts on · send a test</span></button><button class="txt-btn" type="button" data-a="off">Turn off</button><small>You will be told when to rest, when it is safe again, before heavy rain, and about official warnings for '+(atUser()?'where you are':esc(wname()))+'. Quiet from 10 PM to 6 AM except emergencies.</small>'
        :'<button class="btn btn-primary sm" type="button" data-a="on">'+ic('bell')+'<span>Get alerts on this phone</span></button><small>Rest reminders, heavy-rain and official warnings, even with the app closed. No sign-up, no phone number.</small>';}
    row.onclick=function(e){var b=e.target.closest('button');if(!b)return;var a=b.dataset.a;b.disabled=true;
      (a==='on'?pushOn().then(function(){draw(true);toast('Alerts on. Sending a test now');return pushTest();}):a==='off'?pushOff().then(function(){draw(false);toast('Alerts off');}):pushTest().then(function(r){toast('Test sent: '+r.message.title);}))
        .catch(function(er){if(er.message==='ios')return;var m=er.message||'';toast(/Registration failed|push service|AbortError|not supported/i.test(m+er.name)?'This browser cannot receive push alerts here. Try Chrome, Edge or Firefox in a normal (not private) window.':m);}).then(function(){b.disabled=false;});};
    draw(false);curSub().then(function(sub){draw(!!sub&&store('us_push')!==false);if(sub)pushSync();}).catch(function(){});}

  /* ---------- Assistant: chat and voice over the app's live data ---------- */
  function assistant(){
    var lang=store('us_lang')||'en',mute=!!store('us_mute'),hist=[],busy=false,info=null,audio=null,rec=null,greeted=false;
    var SR=window.SpeechRecognition||window.webkitSpeechRecognition;
    var T={en:{title:'Ask Raहत',ph:'Ask about heat, rain, water, routes',hello:'Hi! Ask me if it is safe to work now, where the nearest water or shade is, or which roads are flooded. Tap the mic to talk, in English or Hindi.',listen:'Listening…',fail:'Sorry, I could not answer that: '},
           hi:{title:'राहत से पूछें',ph:'गर्मी, बारिश, पानी, रास्ते के बारे में पूछें',hello:'नमस्ते! पूछिए: क्या अभी काम करना सुरक्षित है, सबसे पास पानी या छाया कहां है, या कौन सी सड़क पर पानी भरा है। बोलने के लिए माइक दबाएं।',listen:'सुन रहा हूं…',fail:'माफ़ कीजिए, जवाब नहीं मिल सका: '}};
    var fab=document.createElement('button');fab.type='button';fab.className='ask-fab';fab.setAttribute('aria-label','Ask Rahat');fab.innerHTML=ic('chat-circle-dots')+'<span>Ask</span>';
    var pn=document.createElement('div');pn.className='ask-panel';pn.hidden=true;pn.setAttribute('role','dialog');pn.setAttribute('aria-label','Rahat assistant');
    pn.innerHTML='<div class="ask-h"><div><b id="askT"></b><small id="askProv">Live Delhi data</small></div><div class="ask-tools"><button type="button" class="ask-ib" id="askLoc">'+ic('crosshair')+'</button><button type="button" class="ask-lang" id="askLang"></button><button type="button" class="ask-ib" id="askSpk"></button><button type="button" class="ask-ib" id="askX" aria-label="Close">'+ic('x')+'</button></div></div>'+
      '<div class="ask-log" id="askLog" aria-live="polite"></div><div class="ask-chips" id="askChips"></div>'+
      '<form class="ask-in" id="askForm"><button type="button" class="ask-mic" id="askMic" aria-label="Talk">'+ic('microphone')+'</button><input id="askTxt" autocomplete="off" maxlength="500" enterkeyhint="send"><button type="submit" class="ask-send" aria-label="Send">'+ic('paper-plane-right')+'</button></form>';
    document.body.appendChild(fab);document.body.appendChild(pn);
    var log=$('#askLog'),inp=$('#askTxt'),mic=$('#askMic'),chips=$('#askChips'),spk=$('#askSpk'),locB=$('#askLoc');
    function locLabel(){var w=S.loc&&userWard();locB.classList.toggle('off',!S.loc);locB.setAttribute('aria-label',w?'Using your location: '+w.name:'Share my location');locB.title=w?'Using your location: '+w.name:'Share my location';}
    function shareLoc(then){bubble('bot',lang==='hi'?'आपकी लोकेशन ले रहा हूं… ब्राउज़र पूछे तो Allow दबाएं।':'Getting your location… tap Allow if the browser asks.');
      locate(true,function(ok){locLabel();if(ok){var w=userWard();bubble('bot',(lang==='hi'?'ठीक है, आप ':'Got it, you are in ')+(w?w.name:'Delhi')+(lang==='hi'?' में हैं।':'.'));if(then)then();}
        else bubble('bot',lang==='hi'?'लोकेशन नहीं मिली। पता बार में ताले के निशान पर क्लिक करके Location चालू करें, या कहें "Karol Bagh से Saket का रास्ता"।':'I could not get your location. Click the lock icon next to the address bar and allow Location, or say "route from Karol Bagh to Saket".','err');});}
    locB.onclick=function(){shareLoc();};setInterval(locLabel,4000);locLabel();
    if(!SR)mic.hidden=true;
    function labels(){$('#askT').textContent=T[lang].title;inp.placeholder=T[lang].ph;$('#askLang').textContent=lang==='hi'?'हिं':'EN';$('#askLang').setAttribute('aria-label',lang==='hi'?'Switch to English':'हिंदी में बदलें');
      spk.innerHTML=ic(mute?'speaker-slash':'speaker-high');spk.classList.toggle('off',mute);spk.setAttribute('aria-label',mute?'Voice replies off':'Voice replies on');
      var sg=(info&&info.suggestions&&info.suggestions[lang])||[];chips.innerHTML='';sg.forEach(function(q){var b=document.createElement('button');b.type='button';b.textContent=q;b.onclick=function(){send(q,false);};chips.appendChild(b);});}
    function scroll(){log.scrollTop=log.scrollHeight;}
    function bubble(role,text,cls){var d=document.createElement('div');d.className='ask-msg '+role+(cls?' '+cls:'');d.textContent=text;log.appendChild(d);scroll();return d;}
    function open(){pn.hidden=false;fab.classList.add('open');if(!greeted){greeted=true;bubble('bot',T[lang].hello);}setTimeout(function(){if(matchMedia('(pointer:fine)').matches)inp.focus();},250);}
    function close(){pn.hidden=true;fab.classList.remove('open');stopVoice();if(rec)rec.abort();}
    fab.onclick=open;$('#askX').onclick=close;
    document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!pn.hidden)close();});
    $('#askLang').onclick=function(){lang=lang==='hi'?'en':'hi';store('us_lang',lang);labels();pushSync();};
    spk.onclick=function(){mute=!mute;store('us_mute',mute);if(mute)stopVoice();labels();};
    get('/api/assistant/info').then(function(r){info=r;$('#askProv').textContent=(r.provider==='offline'?'Live Delhi data':'Live Delhi data · '+r.provider)+(r.voice!=='browser'?' · '+r.voice:'');labels();}).catch(function(){labels();});
    labels();

    function stopVoice(){if(audio){audio.pause();audio=null;}if(window.speechSynthesis)speechSynthesis.cancel();}
    function browserSay(text,l){if(!window.speechSynthesis)return;var u=new SpeechSynthesisUtterance(text);u.lang=l==='hi'?'hi-IN':'en-IN';
      var vs=speechSynthesis.getVoices(),v=vs.filter(function(x){return x.lang===u.lang;})[0]||vs.filter(function(x){return x.lang&&x.lang.indexOf(l==='hi'?'hi':'en-IN')===0;})[0];if(v)u.voice=v;u.rate=1;speechSynthesis.speak(u);}
    function say(text,l){stopVoice();if(info&&info.voice&&info.voice!=='browser'){
        fetch(API+'/api/assistant/speak',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text.slice(0,1400),lang:l})}).then(function(r){
          if(r.status!==200)throw 0;return r.blob();}).then(function(b){audio=new Audio(URL.createObjectURL(b));return audio.play();}).catch(function(){browserSay(text,l);});}
      else browserSay(text,l);}

    function doAction(a){if(a.type==='directions'){window.open(a.url,'_blank','noopener');return;}
      if(a.type==='locate'){var lastQ=null;for(var i=hist.length-1;i>=0;i--)if(hist[i].role==='user'){lastQ=hist[i].text;break;}shareLoc(lastQ?function(){send(lastQ,false);}:null);return;}
      var cmd=document.getElementById('command');if(cmd)cmd.scrollIntoView({behavior:'smooth',block:'start'});if(matchMedia('(max-width:767px)').matches)close();
      if(a.type==='map')openTab('map',{after:function(){setTimeout(function(){window.__liveFocus&&window.__liveFocus({name:a.name,lat:a.lat,lon:a.lon,kind:'drinking_water'});},80);}});
      else if(a.type==='route')openTab('route',{after:function(){setTimeout(function(){window.__routeSet&&window.__routeSet({lat:a.from[0],lon:a.from[1],name:a.from_name},{lat:a.to[0],lon:a.to[1],name:a.to_name});},80);}});
      else if(a.type==='screen')openTab(a.screen);}
    function botReply(r,spoken){var d=bubble('bot',r.reply);
      if(r.actions&&r.actions.length){var acts=document.createElement('div');acts.className='ask-acts';r.actions.forEach(function(a){var b=document.createElement('button');b.type='button';
        b.innerHTML=ic(a.type==='locate'?'crosshair':a.type==='map'?'map-pin':a.type==='directions'?'arrow-right':a.type==='route'?'flag':'arrow-right')+'<span>'+esc(a.label)+'</span>';b.onclick=function(){doAction(a);};acts.appendChild(b);});d.appendChild(acts);}
      var meta=document.createElement('div');meta.className='ask-meta';var pb=document.createElement('button');pb.type='button';pb.setAttribute('aria-label','Read aloud');pb.innerHTML=ic('speaker-high');pb.onclick=function(){say(r.reply,r.lang);};meta.appendChild(pb);
      var src=document.createElement('span');src.textContent=(r.tools_used&&r.tools_used.length?'From live data':'')+(r.provider&&r.provider!=='offline'?' · '+r.provider:'');meta.appendChild(src);d.appendChild(meta);scroll();
      if(spoken&&!mute)say(r.reply,r.lang);
      var au=(r.actions||[]).filter(function(a){return a.auto;})[0];if(au)setTimeout(function(){doAction(au);},700);}
    function send(text,spoken){text=(text||'').trim();if(!text||busy)return;busy=true;inp.value='';if(!greeted){greeted=true;}
      bubble('user',text);hist.push({role:'user',text:text});var wait=document.createElement('div');wait.className='ask-msg bot';wait.innerHTML='<span class="ask-dots"><i></i><i></i><i></i></span>';log.appendChild(wait);scroll();
      var body={messages:hist.slice(-10),ward_id:S.wardId,role:S.role||'rider',lang:lang};if(S.loc&&atUser()){body.lat=+S.loc.lat.toFixed(5);body.lon=+S.loc.lon.toFixed(5);}if(S.loc){body.my_lat=+S.loc.lat.toFixed(5);body.my_lon=+S.loc.lon.toFixed(5);}
      get('/api/assistant/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),timeout:45000}).then(function(r){wait.remove();hist.push({role:'assistant',text:r.reply});botReply(r,spoken);})
        .catch(function(e){wait.remove();hist.pop();bubble('bot',T[lang].fail+e.message,'err');}).then(function(){busy=false;});}
    $('#askForm').onsubmit=function(e){e.preventDefault();send(inp.value,false);};
    mic.onclick=function(){if(rec){rec.stop();return;}stopVoice();rec=new SR();rec.lang=lang==='hi'?'hi-IN':'en-IN';rec.interimResults=true;rec.maxAlternatives=1;var finalT='';
      rec.onstart=function(){mic.classList.add('on');inp.value='';inp.placeholder=T[lang].listen;};
      rec.onresult=function(e){var t='';finalT='';for(var i=0;i<e.results.length;i++){t+=e.results[i][0].transcript;if(e.results[i].isFinal)finalT+=e.results[i][0].transcript;}inp.value=t;};
      rec.onerror=function(e){if(e.error==='not-allowed')toast('Allow the microphone to talk to the assistant');else if(e.error!=='no-speech'&&e.error!=='aborted')toast('Voice input failed: '+e.error);};
      rec.onend=function(){mic.classList.remove('on');inp.placeholder=T[lang].ph;rec=null;var t=(finalT||inp.value).trim();if(t)send(t,true);};
      try{rec.start();}catch(e){rec=null;toast('Voice input is not available');}};
    window.__ask=function(q){open();if(q)send(q,false);};
  }

  /* ---------- boot ---------- */
  setChip('','Connecting');
  function waitWeather(tries){return get('/api/health',{timeout:8000}).then(function(h){if(h.weather&&h.weather.ready)return h;if(tries>40)throw new Error('weather not ready');setChip('','Loading weather');
    return new Promise(function(res){setTimeout(res,3000);}).then(function(){return waitWeather(tries+1);});});}
  waitWeather(0).then(function(){
    window.__live=true;tick();wardTexts();startLocation();assistant();
    if(PUSH_OK&&store('us_push'))swReg().catch(function(){});
    try{var go=new URLSearchParams(location.search).get('go');if(go){var cmd=document.getElementById('command');setTimeout(function(){if(cmd)cmd.scrollIntoView({block:'start'});openTab(go);},400);}}catch(e){}
    $('#legalSrc').textContent='Live data: Open-Meteo (CC BY 4.0), OpenStreetMap (ODbL), NDMA SACHET, DataMeet wards. Earth imagery: NASA.';
    get('/api/meta').then(function(m){S.meta=m;var a=m.aws||{},on=[];if(a.s3_photos)on.push('S3');if(a.rekognition)on.push('Rekognition');if(a.translate)on.push('Translate');if(a.bedrock)on.push('Bedrock');if(a.location)on.push('Location Service');if(a.polly)on.push('Polly');
      if(on.length)$('#legalSrc').textContent='Running on AWS ('+a.region+'): '+on.join(', ')+'. Data: Open-Meteo, OpenStreetMap, NDMA SACHET, ESA WorldCover. Earth imagery: NASA.';}).catch(function(){});
    get('/api/wards/attributes').then(function(a){if(a.landcover_source==='worldcover'){S.attrs=a.wards;var tl=$('#treeLay');if(tl)tl.hidden=false;}}).catch(function(){});
    return Promise.all([loadDash(),loadAlerts(),loadReports()]).then(function(){setChip('on','Live');loadHistory();stream();
      var RF={map:window.__mapRefresh,safe:window.__safeLoad,ops:window.__opsLoad,fc:INIT.fc};Object.keys(inited).forEach(function(t){if(inited[t]&&RF[t])RF[t]();});
      setInterval(function(){if(!document.hidden)loadDash().catch(function(){});},5*60*1000);
      setInterval(function(){if(!document.hidden)loadAlerts().catch(function(){});},2*60*1000);});
  }).catch(function(e){console.warn('Rahat API',e);window.__live=false;Object.keys(DEMO).forEach(function(k){if(!inited[k])INIT[k]=DEMO[k];});setChip('off','Server offline, example data');});
})();
