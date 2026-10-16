"""Rebuild the frozen 2026-08-11 USA or JP release from Git and external inputs."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys

ROOT=Path(__file__).resolve().parents[1]
def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region',choices=['USA','JP'],required=True)
    parser.add_argument('--source-bin',type=Path,required=True)
    parser.add_argument('--subtitle-font',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    source=args.source_bin.resolve();font=args.subtitle_font.resolve();output=args.output_dir.resolve()
    if output.exists():raise SystemExit('Use a new output directory; existing outputs are never overwritten.')
    manifest=json.loads((ROOT/'docs/rebuild/INPUTS.json').read_text(encoding='utf-8'))
    expected=manifest['originals'][args.region]
    if source.stat().st_size!=expected['bytes'] or sha(source)!=expected['sha256']:
        raise SystemExit('Original BIN differs from the supported regional revision.')
    if sha(font)!=manifest['externalSubtitleFont']['sha256']:
        raise SystemExit('Subtitle font differs from the pinned Medium font.')
    for row in manifest['publicFiles']:
        path=ROOT/row['path']
        if not path.is_file() or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']:
            raise SystemExit('Frozen build input differs: '+row['path'])
    output.mkdir(parents=True)
    def run(name,arguments):
        with (output/(name+'.log')).open('w',encoding='utf-8') as log:
            result=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'workspace/scripts'/name),*map(str,arguments)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:raise SystemExit(f'{name} failed; see its log in {output}')
        print('PASS',name,flush=True)
    if args.region=='USA':
        target=output/'final/Bushido Blade (USA) [KO Final 20260811].bin'
        run('build_usa_subtitle.py',[source,'--font',font,'--output-dir',target.parent,'--base-name',target.stem])
        run('build_usa_tim_patch.py',['--in-place',target])
        run('build_usa_ui_patch.py',['--in-place',target])
    else:
        run('build_full_graphics_patch.py',[
            source,'--ui-catalog',ROOT/'workspace/graphics/ui_translation_batch/edit_catalog.json',
            '--title-png',ROOT/'workspace/graphics/title/title_ko_edit.png',
            '--loading-png',ROOT/'workspace/graphics/loading/edited/SLPM_86020_0007657C.png',
            '--ending-dir',ROOT/'workspace/graphics/ending/edited',
            '--font',ROOT/'inputs/fonts/Galmuri11-Condensed.ttf','--output-dir',output/'graphics'])
        graphics=output/'graphics/Bushido Blade (Japan) [Korean Graphics].bin'
        target=output/'final/Bushido Blade (Japan) [KO Final].bin'
        run('build_subtitle_v37.py',[
            graphics,'--font',font,'--output-dir',target.parent,'--base-name',target.stem,
            '--allow-source-sha',sha(graphics),
            '--allow-exe-sha','909CBD027BAA13A6C0FA2B86ADF90FEA6606102EFF2F5A00DB052ED4DB2103AB',
            '--files',*[f'DATA7/V{i:02d}.DAT' for i in range(9)]])
        run('build_jp_gallery_unlock.py',['--in-place',target])
    final_hash=sha(target)
    if final_hash!=manifest['targets'][args.region]:raise SystemExit('Rebuilt BIN differs from the published final target: '+final_hash)
    if sha(source)!=expected['sha256']:raise SystemExit('Original input changed during the build.')
    result={'status':'PASS','region':args.region,'sourceSHA256':expected['sha256'],'finalSHA256':final_hash,
            'finalBytes':target.stat().st_size,'publicInputsVerified':len(manifest['publicFiles']),
            'subtitleFontSHA256':sha(font),'originalUnchanged':True,'runtimeVerification':'not performed'}
    (output/'REBUILD_RESULT.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
    print('FINAL PASS',args.region,final_hash,flush=True)

if __name__=='__main__':main()
