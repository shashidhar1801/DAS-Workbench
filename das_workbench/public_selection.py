"""Selection conversion and source-bound local review, independent of Qt."""
from datetime import datetime,timedelta
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR,ROUND_HALF_EVEN
import re
import math
import numpy as np
from .public_engine import digest


NANOSECOND=Decimal('0.000000001')


def split_timestamp(text):
    """ISO seconds plus exact decimal fraction; do not infer a timezone."""
    m=re.fullmatch(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})?',text.strip())
    if not m:raise ValueError('Use an ISO timestamp with seconds and at most 9 fractional digits, on the native clock')
    whole=datetime.fromisoformat(m[1]+(m[3] or '').replace('Z','+00:00'))
    return whole,Decimal('0.'+(m[2] or '0'))


def timestamp_offset(text,origin):
    value,vf=split_timestamp(text);base,bf=split_timestamp(origin)
    if (value.tzinfo is None)!=(base.tzinfo is None):raise ValueError('Timestamp timezone must match the native origin qualification; do not add or remove a timezone')
    delta=value-base
    return Decimal(delta.days*86400+delta.seconds)+vf-bf


def time_snapshot(signal,start,end):
    """Canonical sample bounds plus their explicitly rounded display tokens."""
    dt=Decimal(str(signal.dt));base,fraction=split_timestamp(signal.origin)
    offset=(fraction+Decimal(start)*dt).quantize(NANOSECOND,rounding=ROUND_HALF_EVEN)
    seconds=int(offset.to_integral_value(rounding=ROUND_FLOOR));ns=int((offset-seconds)*10**9)
    whole=base+timedelta(seconds=seconds)
    zone=whole.strftime('%z');zone=(zone[:3]+':'+zone[3:]) if zone else ''
    if signal.origin.endswith('Z') and zone=='+00:00':zone='Z'
    stamp=whole.strftime('%Y-%m-%dT%H:%M:%S')+f'.{ns:09d}'+zone
    duration=Decimal(end-start)*dt
    return dict(start=start,end=end,origin=signal.origin,dt=str(signal.dt),n=signal.n,
                timestamp=stamp,duration_display=str(duration.quantize(NANOSECOND,rounding=ROUND_HALF_EVEN)),
                duration_exact=str(duration))


def absolute_interval(signal,text,duration_text,snapshot=None):
    """Unchanged displays acknowledge indices; edited times use strict ceil bounds.

    Display uses nearest nanosecond, ties to even. Decimal parsing preserves all
    supported input digits. No enlarged tolerance or snapping of edited times.
    """
    dt=Decimal(str(signal.dt));duration=Decimal(str(duration_text))
    valid=bool(snapshot and snapshot['origin']==signal.origin and snapshot['dt']==str(signal.dt) and snapshot['n']==signal.n)
    same_start=valid and text.strip()==snapshot['timestamp']
    same_duration=valid and duration==Decimal(snapshot['duration_display'])
    start=Decimal(snapshot['start'])*dt if same_start else timestamp_offset(text,signal.origin)
    if same_duration:duration=Decimal(snapshot['duration_exact'])
    if not start.is_finite() or not duration.is_finite() or start<0 or duration<=0 or start+duration>Decimal(signal.n)*dt:
        raise ValueError('Requested timestamp/duration lies outside the native recording; use a positive duration')
    a=int((start/dt).to_integral_value(rounding=ROUND_CEILING));b=int(((start+duration)/dt).to_integral_value(rounding=ROUND_CEILING))
    if not 0<=a<b<=signal.n:raise ValueError('Timestamp interval contains no valid native samples')
    return a,b


def interval_indices(signal,start_seconds,duration_seconds,resolution_s=0):
    if not math.isfinite(start_seconds+duration_seconds) or start_seconds<0 or duration_seconds<=0:raise ValueError('Use a nonnegative start and positive duration')
    def first_at_or_after(seconds):
        value=seconds/signal.dt
        return math.ceil(value-resolution_s/signal.dt-64*np.finfo(float).eps*max(1,abs(value)))
    a=first_at_or_after(start_seconds);b=first_at_or_after(start_seconds+duration_seconds)
    if not 0<=a<b<=signal.n:raise ValueError('Requested time interval lies outside the native recording')
    return a,b


def coordinate_indices(signal,coordinate,low,high,trace_coordinate,resolution=0):
    if low>high:raise ValueError('Coordinate minimum must not exceed maximum')
    v=signal.coordinates[coordinate];ch=np.flatnonzero((v>=low-resolution/2)&(v<=high+resolution/2)).tolist()
    if not ch:raise ValueError('No native channels lie in the requested coordinate interval')
    trace=ch[int(np.argmin(abs(v[ch]-trace_coordinate)))]
    return ch,trace


def review_scope(source,signals):
    if not source.get('sha256'):return None
    return digest(dict(source_sha256=source['sha256'],selections=[{k:s[k] for k in ('group','start','end','channels','trace','coordinate')} for s in signals]))


def qualify_review(recipe,source):
    r=dict(recipe);scope=review_scope(source,r['signals']);valid=bool(scope and r.get('event_review_scope')==scope)
    r['event_review_status']='reviewed selection' if valid else 'unreviewed selection'
    if not valid:
        r['prior_event_review']=r.get('event_review','');r['event_review']='Unreviewed: source or time/channel/coordinate selection differs from the recorded review, or no bound review is supplied.'
    return r
