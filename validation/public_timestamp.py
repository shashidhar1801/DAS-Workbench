"""Real native GUI timestamp regression; exercises the actual QPushButton."""
from decimal import Decimal
from PySide6.QtWidgets import QPushButton
from das_workbench.public_selection import time_snapshot,review_scope
from das_workbench.public_engine import dump


def exercise(w,app,target):
    s=w.signals[0];button=next(b for b in w.findChildren(QPushButton) if b.text()=='Apply timestamp')
    w.channels.setText('0:16');w.trace.setValue(8);w.window.setValue(10*s.dt);w.hop.setValue(5*s.dt);w.fft.setValue(10)
    rows=[]
    def select(a,b):
        w.start.blockSignals(True);w.end.blockSignals(True);w.start.setValue(a);w.end.setValue(b);w.start.blockSignals(False);w.end.blockSignals(False);w.sync_time_fields()
        r=w.recipe();r['event_review']='Timestamp regression selection only; no event inference';r['event_review_scope']=review_scope(s.source,r['signals']);w.recipe_base=r;w.update_review()
    for a in sorted(set([1,*[x for x in [1000,5000] if x+100<=s.n],s.n-100])):
        select(a,a+100);initial=w.absolute_start.text();steps=[]
        assert w.recipe()['event_review_status']=='reviewed selection'
        for repeat in range(10):
            button.click();app.processEvents();assert (w.start.value(),w.end.value())==(a,a+100)
            assert w.recipe()['event_review_status']=='reviewed selection'
            assert Decimal(w.time_display['duration_exact'])==Decimal(100)*Decimal(str(s.dt))
            steps.append([w.start.value(),w.end.value()])
        rows.append(dict(start=a,end=a+100,display=initial,repeat_bounds=steps,exact_duration_s=w.time_display['duration_exact'],review_preserved=True))
    select(1,101)
    invalid=[]
    for txt in ['invalid timestamp','1900-01-01T00:00:00Z',time_snapshot(s,s.n,s.n+100)['timestamp'],w.absolute_start.text().replace('Z','') if w.absolute_start.text().endswith('Z') else '2020-01-01T00:00:00',w.absolute_start.text().replace('.', '.1234567890')]:
        w.absolute_start.setText(txt);button.click();app.processEvents();assert (w.start.value(),w.end.value())==(1,101)
        assert w.status.text().startswith('Error:')
        assert w.recipe()['event_review_status']=='reviewed selection'
        invalid.append(dict(input=txt,error=w.status.text(),bounds=[1,101]))
    select(1,101);w.absolute_start.setText(time_snapshot(s,Decimal('1.25'),Decimal('101.25'))['timestamp']);button.click();app.processEvents()
    assert (w.start.value(),w.end.value())==(2,102)
    assert w.recipe()['event_review_status']=='unreviewed selection'
    offgrid=dict(bounds=[2,102],review='unreviewed selection')
    select(s.n-100,s.n);w.duration.setValue(float(Decimal(101)*Decimal(str(s.dt))));button.click();app.processEvents()
    assert (w.start.value(),w.end.value())==(s.n-100,s.n) and w.status.text().startswith('Error:')
    invalid.append(dict(input='near-end duration extended by one sample',error=w.status.text(),bounds=[s.n-100,s.n]))
    select(1,101);w.duration.setValue(float(Decimal('102.25')*Decimal(str(s.dt))));button.click();app.processEvents()
    assert (w.start.value(),w.end.value())==(1,104) and w.recipe()['event_review_status']=='unreviewed selection'
    record=dict(passed=True,group=s.group,dt=s.dt,n=s.n,origin=s.origin,clock=s.clock,unchanged=rows,invalid=invalid,offgrid=offgrid,edited_duration_bounds=[1,104],button='Apply timestamp',precision='nearest nanosecond display; canonical unchanged bounds; strict decimal ceil for edited times')
    dump(target/'timestamp_checks.json',record);w.grab().save(str(target/'timestamp_checks.png'))
    w.recipe_base={};w.last_error=None
    return record
