import base64,re,json,os
D='/home/claude/urbanshield3d/v2/'
s=open(D+'template.html').read()
js=open('/home/claude/vendor/bklit/bklit-charts.js').read(); css=open('/home/claude/vendor/bklit/bklit-charts.css').read()
assert '</script' not in js.lower() and '</style' not in css.lower()
s=s.replace('__BKLIT_JS__',js).replace('__BKLIT_CSS__',css)
IC='/tmp/claude-0/node_modules/@phosphor-icons/core/assets/regular/'
def icon(n):
    sv=open(IC+n+'.svg').read().strip()
    return sv.replace('<svg ','<svg class="ic" aria-hidden="true" ',1)
s=re.sub(r'\{\{i:([a-z0-9-]+)\}\}',lambda m:icon(m.group(1)),s)
used=['thermometer','warning-circle','drop','buildings','check','cloud-rain','sun','cloud-sun','map-pin','plus','minus','crosshair','house','flag','lock-simple','arrow-right','umbrella-simple','tree-evergreen']
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
s=s.replace('__INDIA__',open(D+'india.json').read().strip())
assert '{{i:' not in s and '__ICONS__' not in s
open(D+'index.html','w').write(s)
print(round(len(s)/1e6,2),'MB','emdash',s.count('—'),s.count('–'))
