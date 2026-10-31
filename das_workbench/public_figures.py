"""Publication figures reconstructed only from saved public analysis products."""
from pathlib import Path
import json, shutil, os, uuid, csv
from datetime import datetime
import numpy as np
import h5py
from matplotlib.figure import Figure
from matplotlib import rc_context
from .native import sha
from .presentation import signal_name
from .public_engine import load_result,evidence,dump

STYLE={'font.family':'DejaVu Sans','font.size':8,'axes.titlesize':9,'axes.labelsize':8,
       'xtick.labelsize':7,'ytick.labelsize':7,'pdf.fonttype':42,'svg.fonttype':'none',
       'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#718096',
       'axes.labelcolor':'#23374d','text.color':'#23374d','path.simplify':True}


def db(x,ref=1):
    with np.errstate(divide='ignore',invalid='ignore'): return 10*np.log10(x/ref)


def caption(directory):
    m,r=load_result(directory);parts=[r.get('title','Recording analysis')+'.']
    parts.append('Source DOI '+str(m['source'].get('doi'))+'; source identity and decoded selection hashes in manifest. CPU float64. No additional filtering. Numerical validation: '+evidence(directory)['status']+'.')
    for x in m['signals']:
        s=x['metadata'];p=x['accepted'];sel=x['selection'];q=x['quality_summary']
        parts.append(f"{s['group']}: {s['quantity']} [{s['unit']}]. {s['calibration']}. {s['clock']}; origin {s['origin']}; native indices [{sel['start']}, {sel['end']}) at {s['fs']:.12g} Hz. Channels: {len(sel['channels'])}, native array indices {sel['channels'][0]}–{sel['channels'][-1]}; trace {sel['trace']}; coordinate {sel['coordinate']}. Coverage is the selected recorded section, not the whole cable. Per-channel population RMS and waveform use the valid native selection-wide mean; {q['excluded']} nonfinite values excluded. {q['integer_extreme_count']} integer-extreme values retained; no guessed sentinel mask. Display waterfall: every {sel.get('display_stride',1)} selected channel, {sel.get('time_bin',1)}-sample means only; final partial bin retained. Native trace/RMS/spectra are not display averaged. PSD/STFT: periodic Hann w[j]=0.5-0.5cos(2πj/N), N={p['nperseg']} ({p['window_actual_s']:.9g} s), hop={p['hop']} ({p['hop_actual_s']:.9g} s), FFT={p['nfft']}, df={p['df']:.9g} Hz, ENBW={p['enbw_hz']:.9g} Hz; frame mean removed, one-sided density [{s['unit']}]²/Hz, mean Welch, {q['spectral_valid_frames']} complete valid frames and {q['spectral_rejected_frames']} rejected. No boundary padding. Nyquist {p['nyquist']:.9g} Hz; displayed 0–{sel['fmax']:.9g} Hz. Zero spectral power is -infinity and uncoloured on dB displays, without a data floor.")
        if p['pressure_window_samples']:
            parts.append(f"Received levels: p_analysis is native pressure minus one selection-wide mean, no filter. Fixed nonoverlapping {p['pressure_window_samples']}-sample ({p['pressure_window_actual_s']:.9g} s) complete valid windows; mean square before 10log10(P/1e-12 Pa²). Incomplete/invalid windows omitted; coverage saved numerically. Recorded digital band0–{p['nyquist']/1000:g}kHz; effective analogue/calibration bandwidth unknown. Spectrogram cropping does not band-limit these levels. Source reference is not received pressure.")
        parts.append('Metadata findings: '+s['metadata'].get('findings',s['metadata'].get('coordinate_finding','See metadata decisions'))+'.')
    parts.append('Waterfall colour: symmetric 98th percentile of absolute finite display values. Spectrogram colour limits saved in recipe when fixed; otherwise peak−70 to peak dB per group. Linear frequency axes. Figures regenerated from full saved selections; zoom does not change export.')
    return '\n\n'.join(parts)+'\n'


def figure(directory,presentation=None):
    if presentation is None and (Path(directory)/'presentation.json').exists():presentation=json.loads((Path(directory)/'presentation.json').read_text())
    if presentation and presentation.get('profile')!='archive':return publication_figure(directory,presentation)
    m,r=load_result(directory);layout=r.get('layout','case');d=Path(directory)
    with rc_context(STYLE):
        fig=Figure(figsize=(25/2.54,(23 if layout=='aligned' else 20)/2.54),dpi=120,layout='constrained')
        title=r.get('title','Native recording')
        fig.suptitle(title+'\n'+r.get('subtitle','Selected recording'),fontsize=12,fontweight='bold')
        with h5py.File(d/'numeric.h5') as h:
            if layout=='aligned':
                grid=fig.add_gridspec(4,2)
                origin=datetime.fromisoformat(m['signals'][0]['metadata']['origin'].replace('Z','+00:00'))
                base=m['signals'][0]['selection']['start']*m['signals'][0]['metadata']['dt']
                for i,x in enumerate(m['signals']):
                    g=h[f'signal{i}'];s=x['metadata'];offset=(datetime.fromisoformat(s['origin'].replace('Z','+00:00'))-origin).total_seconds()-base
                    wave(fig.add_subplot(grid[i,0]),g,x,offset,chr(65+2*i))
                    spec(fig,fig.add_subplot(grid[i,1]),g,x,offset,chr(66+2*i))
                rec=next(i for i,x in enumerate(m['signals']) if x['metadata']['received_pressure']);g=h[f'signal{rec}'];x=m['signals'][rec]
                psd(fig.add_subplot(grid[3,0]),g,x,'G')
                ax=fig.add_subplot(grid[3,1]);dt=x['metadata']['dt'];p=x['accepted'];levels=g['pressure/level_db'][:];tt=(g['pressure/starts'][:]+p['pressure_window_samples']/2)*dt
                ax.plot(tt,np.where(np.isfinite(levels),levels,np.nan),color='#14857d',lw=.8);ax.set(title='H  Received levels · '+str(p['pressure_window_actual_s'])+' s windows',xlabel='Time from selected common start (s)',ylabel='dB re 1 µPa');ax.grid(alpha=.2)
            elif layout=='comparison':
                grid=fig.add_gridspec(2,1)
                for i,x in enumerate(m['signals']): spec(fig,fig.add_subplot(grid[i,0]),h[f'signal{i}'],x,0,chr(65+i))
            else:
                grid=fig.add_gridspec(3,2,height_ratios=[1.1,.75,1])
                x=m['signals'][0];g=h['signal0'];sel=x['selection'];s=x['metadata'];dt=s['dt']
                ax=fig.add_subplot(grid[0,:]);z=g['waterfall'][:];coord=g['waterfall_coordinate'][:];edges=g['bin_edges'][:]*dt+sel['start']*dt
                lim=float(np.percentile(abs(z[np.isfinite(z)]),98)) if np.isfinite(z).any() else 1;lim=lim or 1
                ce=edges_for(coord);im=ax.pcolormesh(edges,ce,z,cmap='RdBu_r',vmin=-lim,vmax=lim,shading='flat',rasterized=True)
                ax.set(title='A  Selected channel section · mean-removed native amplitude',xlabel='Time from recording origin (s)',ylabel=sel['coordinate']);fig.colorbar(im,ax=ax,label=s['unit'],pad=.01)
                wave(fig.add_subplot(grid[1,0]),g,x,0,'B')
                ax=fig.add_subplot(grid[1,1]);ax.plot(g['coordinate'][:],g['rms'][:],lw=.7,color='#42659b');ax.set(title='C  Native per-channel population RMS',xlabel=sel['coordinate'],ylabel=s['unit']);ax.grid(alpha=.2)
                spec(fig,fig.add_subplot(grid[2,0]),g,x,0,'D');psd(fig.add_subplot(grid[2,1]),g,x,'E')
        return fig


def edges_for(x):
    x=np.asarray(x,dtype=float)
    if len(x)==1:return np.array([x[0]-.5,x[0]+.5])
    return np.r_[x[0]-(x[1]-x[0])/2,(x[:-1]+x[1:])/2,x[-1]+(x[-1]-x[-2])/2]


def wave(ax,g,x,offset,letter):
    s=x['metadata'];sel=x['selection'];t=g['trace_time'][:]+offset;y=g['trace'][:]
    ax.plot(t,y,lw=.5,color='#14857d',rasterized=len(t)>100000)
    ax.set(title=f"{letter}  Mean-removed waveform · channel {sel['trace']}",xlabel='Time from '+('common selected start' if offset else 'recording origin')+' (s)',ylabel=s['unit']);ax.grid(alpha=.2)


def spec(fig,ax,g,x,offset,letter):
    s=x['metadata'];sel=x['selection'];p=x['accepted'];freq=g['spectral/frequency'][:];keep=freq<=sel['fmax'];z=g['spectral/density'][:,keep].T
    ref=1e-12 if s['received_pressure'] else 1;z=db(z,ref);finite=z[np.isfinite(z)];hi=float(finite.max()) if len(finite) else 0
    limits=sel.get('spectral_db_limits',[hi-70,hi]);tt=(g['spectral/frame_starts'][:]+sel['start']+p['nperseg']/2)*s['dt']+offset
    frequency_edges=edges_for(freq[keep]);frequency_edges[0]=max(0,frequency_edges[0]);frequency_edges[-1]=min(sel['fmax'],frequency_edges[-1])
    im=ax.pcolormesh(edges_for(tt),frequency_edges,np.ma.masked_invalid(z),vmin=limits[0],vmax=limits[1],cmap='magma',shading='flat',rasterized=True)
    ax.set(title=f"{letter}  Spectrogram · {p['window_actual_s']:.4g} s Hann window",xlabel='Time (s)',ylabel='Frequency (Hz)',ylim=(0,sel['fmax']))
    ax.set_xlim(sel['start']*s['dt']+offset,sel['end']*s['dt']+offset)
    label='dB re 1 µPa²/Hz' if s['received_pressure'] else 'dB re 1 ('+s['unit']+')²/Hz'
    fig.colorbar(im,ax=ax,label=label,pad=.02)


def psd(ax,g,x,letter):
    s=x['metadata'];freq=g['spectral/frequency'][:];y=g['spectral/psd'][:];ref=1e-12 if s['received_pressure'] else 1
    yy=db(y,ref);visible=freq<=x['selection']['fmax']
    ax.plot(freq[visible],np.where(np.isfinite(yy[visible]),yy[visible],np.nan),lw=.8,color='#42659b')
    ax.set(title=f"{letter}  Power spectral density (Welch)",xlabel='Frequency (Hz)',ylabel='dB re 1 µPa²/Hz' if s['received_pressure'] else 'dB re 1 ('+s['unit']+')²/Hz',xlim=(0,x['selection']['fmax']));ax.grid(alpha=.2)


def export(directory,destination,progress=lambda x:None,control=lambda:None,presentation=None):
    src=Path(directory);dest=Path(destination);load_result(src)
    if dest.exists():raise FileExistsError(dest)
    temp=dest.with_name('.'+dest.name+'.'+uuid.uuid4().hex);temp.mkdir(parents=True)
    try:
        for name in ['numeric.h5','manifest.json','recipe.json','root_map_template.json']:
            shutil.copy2(src/name,temp/name)
        m,r=load_result(src)
        if 'validation' in m:
            if evidence(src)['status']=='rejected':raise ValueError('Rejected validation cannot be exported')
            shutil.copy2(src/m['validation']['file'],temp/m['validation']['file'])
        if presentation is None and (src/'presentation.json').exists():presentation=json.loads((src/'presentation.json').read_text())
        if presentation:
            presentation=dict(presentation,version='publication-layout-2',width_cm=18 if presentation.get('profile')!='archive' else 25,height_cm={'marine':12,'aligned':17,'resolution':13,'spatial':16}.get(presentation.get('profile'),23 if r.get('layout')=='aligned' else 20))
            dump(temp/'presentation.json',presentation)
        (temp/'caption.txt').write_text(caption(src)+('\nPresentation: '+json.dumps(presentation)+'; layout only, scientific arrays and recipe retained. Figure dimensions and processing settings are recorded in the export metadata.\n' if presentation else ''))
        with h5py.File(src/'numeric.h5') as h:
            for k in h:
                g=h[k]
                with open(temp/(k+'_statistics.csv'),'w',newline='') as f:
                    w=csv.writer(f);w.writerow(['channel_index','coordinate','valid_N','excluded_N','mean','population_RMS','integer_extreme_N'])
                    w.writerows(zip(g['channels'][:],g['coordinate'][:],g['valid_count'][:],g['excluded_count'][:],g['mean'][:],g['rms'][:],g['extreme_count'][:]))
        fig=figure(src,presentation)
        with rc_context(STYLE):
            for ext in ['png','pdf','svg']:
                control();progress('Exporting '+ext.upper());fig.savefig(temp/('figure.'+ext),dpi=300)
        fig.clear();dump(temp/'bundle_checksums.json',{p.name:sha(p) for p in sorted(temp.iterdir()) if p.is_file()})
        control();os.replace(temp,dest);return str(dest)
    finally:
        if temp.exists():shutil.rmtree(temp)


PUBLICATION_STYLE={**STYLE,'font.size':9,'axes.titlesize':9,'axes.labelsize':9,'xtick.labelsize':8,'ytick.labelsize':8}


def publication_figure(directory,presentation):
    """Final-size, restrained views of exactly the saved scientific products."""
    m,r=load_result(directory);profile=presentation['profile']
    if profile not in ('marine','aligned','resolution','spatial'):raise ValueError('Unknown publication presentation')
    height={'marine':12,'aligned':17,'resolution':13,'spatial':16}[profile]
    with rc_context(PUBLICATION_STYLE):
        fig=Figure(figsize=(18/2.54,height/2.54),dpi=120,layout='constrained')
        with h5py.File(Path(directory)/'numeric.h5') as h:
            if profile=='marine':
                x=m['signals'][0];g=h['signal0'];grid=fig.add_gridspec(2,2,height_ratios=[1.2,1])
                ax=fig.add_subplot(grid[0,:]);spec(fig,ax,g,x,0,'A');ax.set_title('A  '+{'10.5281/zenodo.15008561':'MedSea','10.5281/zenodo.5823343':'Svalbard','10.5281/zenodo.18851481':'Trondheim'}.get(m['source'].get('doi'),'Native recording')+' · selected-channel spectrogram',loc='left')
                ax=fig.add_subplot(grid[1,0]);wave(ax,g,x,0,'B');ax.set_title('B  Mean-removed trace',loc='left')
                ax.set_xlabel('Recording time (s)')
                ax=fig.add_subplot(grid[1,1]);psd(ax,g,x,'C');ax.set_title('C  Mean Welch PSD',loc='left')
            elif profile=='spatial':
                x=m['signals'][0];g=h['signal0'];sel=x['selection'];meta=x['metadata'];grid=fig.add_gridspec(3,1,height_ratios=[1.15,1.15,.85])
                ax=fig.add_subplot(grid[0]);waterfall(fig,ax,g,x,'A')
                ax=fig.add_subplot(grid[1]);spec(fig,ax,g,x,0,'B');ax.set_title('B  Selected-channel spectrogram',loc='left');ax.set_xlabel('Recording time (s)')
                ax=fig.add_subplot(grid[2]);psd(ax,g,x,'C');ax.set_title('C  Power spectral density (Welch)',loc='left')
            elif profile=='aligned':
                if len(m['signals'])!=3:raise ValueError('Aligned publication layout requires three signals')
                grid=fig.add_gridspec(4,1);first=m['signals'][0];origin=datetime.fromisoformat(first['metadata']['origin'].replace('Z','+00:00'));base=first['selection']['start']*first['metadata']['dt'];duration=(first['selection']['end']-first['selection']['start'])*first['metadata']['dt']
                for i,x in enumerate(m['signals']):
                    offset=(datetime.fromisoformat(x['metadata']['origin'].replace('Z','+00:00'))-origin).total_seconds()-base
                    ax=fig.add_subplot(grid[i]);wave(ax,h[f'signal{i}'],x,offset,chr(65+i));ax.set_title(chr(65+i)+'  '+signal_name(x['metadata']),loc='left');ax.set_xlabel('');ax.set_xlim(0,duration)
                rec=next(i for i,x in enumerate(m['signals']) if x['metadata']['received_pressure']);x=m['signals'][rec];g=h[f'signal{rec}'];p=x['accepted'];tt=(g['pressure/starts'][:]+p['pressure_window_samples']/2)*x['metadata']['dt']
                ax=fig.add_subplot(grid[3]);ax.plot(tt,g['pressure/level_db'][:],lw=.9,color='#14857d');ax.set(title=f"D  Received sound pressure level ({p['pressure_window_actual_s']:g} s)",xlabel='Time from selected common start (s)',ylabel='dB re 1 µPa',xlim=(0,duration));ax.grid(alpha=.2)
            else:
                if len(m['signals'])!=2:raise ValueError('Resolution layout requires two spectra')
                grid=fig.add_gridspec(2,1)
                for i,x in enumerate(m['signals']):
                    ax=fig.add_subplot(grid[i]);spec(fig,ax,h[f'signal{i}'],x,0,chr(65+i));ax.set_title(f"{chr(65+i)}  Hann window: {x['accepted']['window_actual_s']:.3f} s",loc='left')
        for ax in fig.axes:
            if ax.get_title(loc='left'):ax.set_title('',loc='center')
        return fig


def waterfall(fig,ax,g,x,letter):
    sel=x['selection'];s=x['metadata'];dt=s['dt'];z=g['waterfall'][:];coord=g['waterfall_coordinate'][:]
    edges=g['bin_edges'][:]*dt+sel['start']*dt
    limit=float(np.percentile(abs(z[np.isfinite(z)]),98)) if np.isfinite(z).any() else 1
    limit=limit or 1
    im=ax.pcolormesh(edges,edges_for(coord),z,cmap='RdBu_r',vmin=-limit,vmax=limit,shading='flat',rasterized=True)
    ax.set_title(letter+'  Cable-section amplitude',loc='left')
    ax.set(xlabel='Recording time (s)',ylabel=sel['coordinate'])
    fig.colorbar(im,ax=ax,label=s['unit'],pad=.02)
    return im
