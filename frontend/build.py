"""Build the single-file site: python frontend/build.py  ->  frontend/index.html (copy it to web/index.html)."""
import base64,re,json,os
D=os.path.dirname(os.path.abspath(__file__))+'/'
s=open(D+'template.html').read()
js=open(D+'vendor/bklit-charts.js').read(); css=open(D+'vendor/bklit-charts.css').read()
assert '</script' not in js.lower() and '</style' not in css.lower()
s=s.replace('__BKLIT_JS__',js).replace('__BKLIT_CSS__',css)
IC=D+'vendor/icons/'  # Phosphor Icons (MIT), regular weight
def icon(n):
    sv=open(IC+n+'.svg').read().strip()
    return sv.replace('<svg ','<svg class="ic" aria-hidden="true" ',1)
s=re.sub(r'\{\{i:([a-z0-9-]+)\}\}',lambda m:icon(m.group(1)),s)
used=['thermometer','warning-circle','drop','buildings','check','cloud-rain','sun','cloud-sun','map-pin','plus','minus','crosshair','house','flag','lock-simple','arrow-right','umbrella-simple','tree-evergreen','chat-circle-dots','microphone','paper-plane-right','speaker-high','speaker-slash','x','bell','bell-ringing']
s=s.replace('__ICONS__',json.dumps({n:icon(n) for n in used}))
s=s.replace('__DELHI__',open(D+'data/delhi.json').read())
ph={k:'data:image/jpeg;base64,'+base64.b64encode(open(D+'photos/'+k+'.jpg','rb').read()).decode() for k in ['worker','commuter','officer','planner']}
s=s.replace('__PHOTOS__',json.dumps(ph))
s=s.replace('__HEROIMG__',json.dumps('data:image/jpeg;base64,'+base64.b64encode(open(D+'photos/hero.jpg','rb').read()).decode()))
for f in ['earth-day','earth-night','earth-water','clouds','earth-topo']:
  n='assets/'+f+'.jpg'; assert "'"+n+"'" in s
  s=s.replace("'"+n+"'","'data:image/jpeg;base64,"+base64.b64encode(open(D+n,'rb').read()).decode()+"'")
lj=open(D+'live.js').read();assert '</script' not in lj.lower()
s=s.replace('__LIVEJS__',lj)
s=s.replace('__RAVIPOSTER__','data:image/jpeg;base64,'+base64.b64encode(open(D+'media/ravi-poster.jpg','rb').read()).decode())
s=s.replace('__INDIA__',open(D+'india.json').read().strip())
s=s.replace('__LOGO__','data:image/webp;base64,'+base64.b64encode(open(D+'assets/logo-480.webp','rb').read()).decode())
s=s.replace('__SUN__','data:image/png;base64,'+base64.b64encode(open(D+'assets/sun-64.png','rb').read()).decode())
assert '__LOGO__' not in s and '__SUN__' not in s
assert '{{i:' not in s and '__ICONS__' not in s
open(D+'index.html','w').write(s)
print(round(len(s)/1e6,2),'MB','emdash',s.count('—'),s.count('–'))
