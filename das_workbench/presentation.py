"""Reader-facing terminology; serialized profile and scientific keys stay stable."""
import html
import numpy as np

FORMAT_NAMES={'asn-hdf5-1':'ASN HDF5','svalbard-mat5-1':'MAT v5 with channel and time vectors','lake-hdf5-2':'HDF5 with associated signal metadata'}


def signal_name(metadata):
    q=metadata['quantity']
    if q=='Published strain-rate integers (scaling already applied)' and metadata.get('metadata',{}).get('publication_provenance_verified'):
        return 'DAS strain rate'
    return q


def status_name(value):
    return {'passed':'Passed','failed':'Failed','rejected':'Failed','not supplied':'Not run','not applicable':'Not applicable'}.get(value,'Not run')


def plan_text(p, signals, recipe):
    lines=['Ready to run' if p['allowed'] else 'Selection needs adjustment']
    for accepted,selected in zip(p['signals'],recipe['signals']):
        s=next(s for s in signals if s.group==selected['group'])
        n=accepted['n'];c=accepted['channels'];frames=accepted['frames'];nf=accepted['nfft']//2+1
        storage=(accepted['waterfall_pixels']*8+n*(np.dtype(s.dtype).itemsize+24)+c*(90+8*len(s.channels))+
                 accepted['waterfall_rows']*16+(accepted['waterfall_columns']+1)*8+accepted['nperseg']*8+nf*16+frames*(nf*8+17))
        if accepted['pressure_window_samples']:storage+=(n//accepted['pressure_window_samples'])*25
        lines.append(f'Numerical array payload: {storage/1024**2:.2f} MiB before compression; file headers, metadata and figures additional')
        lines.extend([f"{signal_name(s.summary())} [{s.unit}]",
          f"Samples [{accepted['start']}, {accepted['end']}); {accepted['channels']:,} channels; trace {selected['trace']}",
          f"Recording time {accepted['start']*s.dt:.9g}–{accepted['end']*s.dt:.9g} s",
          f"Window {accepted['window_actual_s']:.9g} s ({accepted['nperseg']} samples); step {accepted['hop_actual_s']:.9g} s ({accepted['hop']} samples)",
          f"FFT {accepted['nfft']} samples; bin spacing {accepted['df']:.9g} Hz; ENBW {accepted['enbw_hz']:.9g} Hz",
          f"Display: {selected.get('time_bin',1)}-sample means, every {selected.get('display_stride',1)} selected channel",
          f"Estimated numerical buffers {accepted['numerical_buffer_estimate']/1024**2:.2f} MiB; logical input {accepted['native_logical_bytes']/1024**2:.2f} MiB"])
    lines.append('Buffer budget 256 MiB; physical disk reads and figure memory are separate. CPU · float64.')
    lines.extend(p['blocking_reasons'])
    return '\n'.join(lines)


def verification_html(validation):
    esc=lambda x:html.escape(str(x));state=status_name(validation.get('status'))
    parts=[f'<h3>Numerical verification: {state}</h3>',
           '<p>Scope: selected native samples and saved numerical arrays.</p>',
           '<p>Reference: separate CPU float64 implementation. This checks numerical agreement, not physical calibration or event identity.</p>']
    report=validation.get('report',{})
    checks=report.get('checks',{})
    if checks:
        categories={}
        for key,value in checks.items():
            key=key.replace('_',' ')
            if any(t in key for t in ['digest','source signature','source unchanged']):category='Source and result integrity'
            elif any(t in key for t in ['spectral','frame','frequency','window','accepted samples']):category='Spectral definitions and density'
            elif any(t in key for t in ['time domain','quality']):category='Native-sample statistics and quality'
            elif any(t in key for t in ['native','decoded']):category='Native samples and metadata'
            else:category='Time, channel and coordinate selection'
            categories.setdefault(category,[]).append(value)
        parts.append('<h4>Checks</h4><ul>'+''.join('<li>'+esc(k)+': '+('Passed' if all(v is True for v in values) else 'Failed')+f' ({len(values)})</li>' for k,values in categories.items())+'</ul>')
    elif state=='Not run':parts.append('<p>Run Numerical verification after linking the matching recording.</p>')
    elif state=='Failed':parts.append('<p>The numerical report or its source/result binding could not be accepted. Open details for the diagnostic report.</p>')
    parts.append('<p>Full tolerances, reference code identity and source hashes are available in Show details and the exported sidecar.</p>')
    return ''.join(parts)


def concise_error(error):
    """Keep paths/tracebacks out of ordinary status; retain verbatim diagnostics."""
    msg=str(error).strip().splitlines()[-1] if str(error).strip() else 'Unknown error'
    if any(x in msg for x in ['/Users/','/Volumes/','/var/','Traceback','FileNotFoundError','PermissionError','FileExistsError']):
        return 'The operation could not access its input or output. Check the recording and choose an unused, writable output folder. Details are available.'
    return msg[:300]+' · Details available'


def about_text():
    return ('DAS Workbench 0.3.4.post1\nLocal recording analysis and reproducible figure export.\n\n'
            'Tested platform: macOS arm64. Native recording analysis: CPU float64.\n'
            'Windows/NVIDIA and independent clean installation remain untested.\n'
            'MIT application license; bundled third-party notices apply.\n\n'
            'Maintainer: Shashidhar Siddagangaiah\n'
            'Department of Systems Engineering and Naval Architecture\nNational Taiwan Ocean University, Keelung, Taiwan\n'
            'shashi.18j@gmail.com\nORCID: 0000-0001-7248-6538\n'
            'Public distribution details and tutorial: see the release README.\n'
            'No archival DOI has been assigned.')
