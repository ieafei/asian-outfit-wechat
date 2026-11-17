"""Build the lean WeChat deliverable from one temporary editorial draft."""
import argparse
import colorsys
import hashlib
import html
import json
import statistics
from pathlib import Path

from PIL import Image, ImageOps


def paragraphs(value):
    return [value] if isinstance(value, str) else (value or [])


def normalize_accent(value):
    """Accept an explicit heading colour from the draft, kept legible on a light page."""
    if not isinstance(value, str):
        return None
    text = value.strip().lstrip('#')
    if len(text) != 6 or any(c not in '0123456789abcdefABCDEF' for c in text):
        return None
    red, green, blue = (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    hue, lightness, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
    lightness = min(max(lightness, 0.22), 0.52)
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return '{:02x}{:02x}{:02x}'.format(round(red * 255), round(green * 255), round(blue * 255))


def accent_from_images(images):
    """One readable accent colour for this issue's headings, taken from its photographs.

    Walls and floors own the largest area but carry no hue, so each photograph votes
    only with its saturated pixels, and every photograph gets an equal vote. The hue
    that keeps coming back across the set is the outfit's own colour; a painting or a
    prop that appears in one frame cannot outvote it.
    """
    votes = {}
    totals = {}
    for image in images:
        small = image.copy()
        small.thumbnail((96, 96))
        raw = small.tobytes()
        local = {}
        for i in range(0, len(raw), 3):
            hue, lightness, saturation = colorsys.rgb_to_hls(raw[i] / 255, raw[i + 1] / 255, raw[i + 2] / 255)
            if saturation < 0.18 or lightness < 0.15 or lightness > 0.88:
                continue
            bucket = int(hue * 360) // 12
            local.setdefault(bucket, [0.0, 0.0, 0.0])
            weight = saturation * saturation
            local[bucket][0] += weight
            local[bucket][1] += lightness * weight
            local[bucket][2] += saturation * weight
        if not local:
            continue
        scale = sum(entry[0] for entry in local.values())
        for bucket, entry in local.items():
            votes.setdefault(bucket, [0.0, 0.0, 0.0])
            votes[bucket][0] += entry[0] / scale
            votes[bucket][1] += entry[1] / scale
            votes[bucket][2] += entry[2] / scale
        totals[len(votes)] = True
    if not votes:
        return '624a3d'
    bucket = max(votes, key=lambda k: votes[k][0])
    weight, light_sum, sat_sum = votes[bucket]
    lightness = light_sum / weight
    saturation = sat_sum / weight
    hue = (bucket * 12 + 6) / 360
    # Clamp so the heading stays legible on the light page and on white in the editor.
    saturation = min(max(saturation, 0.32), 0.72)
    lightness = min(max(lightness, 0.30), 0.44)
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return '{:02x}{:02x}{:02x}'.format(round(r * 255), round(g * 255), round(b * 255))


def accent_from_draft(draft, output):
    images = []
    for section in draft.get('sections') or []:
        for photo in section.get('photos') or []:
            path = (output / photo['file']).resolve()
            if path.suffix.lower() not in {'.jpg', '.jpeg', '.png'} or not path.is_file():
                continue
            try:
                with Image.open(path) as source:
                    images.append(source.convert('RGB'))
            except OSError:
                continue
    return accent_from_images(images)


def build(directory, draft_path):
    output = Path(directory).resolve()
    draft = json.loads(Path(draft_path).read_text(encoding='utf-8-sig'))
    source_data = json.loads((output / 'sources.json').read_text(encoding='utf-8-sig'))
    records = source_data if isinstance(source_data, list) else [{**source_data.get('common', {}), **r} for r in source_data['images']]
    by_file = {r['file']: dict(r) for r in records}
    accent = normalize_accent(draft.get('accent'))
    accent_source = 'draft'
    if accent is None:
        accent = accent_from_draft(draft, output)
        accent_source = 'auto'
    escape = html.escape
    pstyle = 'margin:0 0 20px;padding:0;font-size:16px;line-height:1.9;color:#3d3936;text-align:center;'
    cstyle = 'margin:8px 0 24px;padding:0;font-size:12px;line-height:1.8;color:#887b72;text-align:center;'
    body, ordered, photos, captions = [], [], [], []

    def add(text, style=pstyle):
        body.append(f'<p style="{style}">{escape(text)}</p>')

    introduction = paragraphs(draft['intro'])
    if not introduction or not str(introduction[0]).strip():
        raise ValueError('The article needs a first paragraph before the collage.')
    # The opening paragraph describes the piece itself; a handle or platform name
    # there would put the creator's identity ahead of the outfit.
    lead = str(introduction[0])
    for forbidden in ('@', '小红书', '抖音', '微博'):
        if forbidden in lead:
            raise ValueError('The opening paragraph must not name the creator handle or platform: ' + forbidden)
    collage_caption = draft.get('collage_caption') or f'本组{len(records)}张照片的穿搭与姿态整合。'
    for index, text in enumerate(introduction):
        add(text)
        if index == 0:
            # The collage is the cover asset, not an article illustration: it gets the
            # upload marker and no caption.
            add('【整合拼图：请在此上传 封面_整合图.jpg，上传后只删除本提示行】')
    for section in draft['sections']:
        body.append('<p style="margin:30px 0 16px;font-size:18px;line-height:1.7;text-align:center;color:#' + accent + ';"><strong>' + escape(section['heading']) + '</strong></p>')
        for text in paragraphs(section['before']):
            add(text)
        for photo in section['photos']:
            relative = photo['file']
            image_path = (output / relative).resolve()
            if image_path.parent != (output / 'images').resolve() or image_path.suffix.lower() not in {'.jpg', '.jpeg', '.png'}:
                raise ValueError('Each photo must be a JPG/PNG directly inside images/.')
            if relative in [r['file'] for r in ordered]:
                raise ValueError('Do not repeat photographs.')
            record = by_file[relative]
            if record.get('sha256') and hashlib.sha256(image_path.read_bytes()).hexdigest() != record['sha256']:
                raise ValueError('The confirmed image changed: ' + relative)
            with Image.open(image_path) as source:
                im = source.convert('RGB')
            if record.get('dimensions') and list(im.size) != record['dimensions']:
                raise ValueError('Image dimensions changed: ' + relative)
            number = len(ordered) + 1
            # The plate number stays in the upload marker; the caption itself is prose
            # addressed to the reader, so it carries no editorial numbering.
            caption = photo["caption"]
            add(f'【配图{number:02}：请在此上传 {relative}，上传后只删除本提示行】')
            add(caption, cstyle)
            captions.append(caption)
            record.update(article_order=number, upload_file=relative, caption=photo['caption'])
            ordered.append(record)
            photos.append((im, photo.get('focus', [0.5, 0.5])))
        for text in paragraphs(section.get('after')):
            add(text)
    if not 3 <= len(ordered) <= 6 or set(by_file) != {r['file'] for r in ordered}:
        raise ValueError('Use exactly the confirmed 3–6 photographs recorded in sources.json.')
    for text in paragraphs(draft.get('ending')):
        add(text)
    source_names = {(r['platform'], r['creator']) for r in ordered}
    if len(source_names) != 1:
        raise ValueError('The article must use one creator and source platform.')
    platform, creator = next(iter(source_names))
    credit = f'图片来源：{platform}「{creator}」。'
    add(credit, cstyle)
    for key in ['title', 'comment']:
        if not isinstance(draft.get(key), str) or not draft[key].strip():
            raise ValueError('Missing ' + key)

    count = len(photos)
    columns = 2 if count == 4 else (3 if count == 6 else count)
    rows = (count + columns - 1) // columns
    width = 450
    height = round(width * statistics.median(im.height / im.width for im, _ in photos))
    cover = Image.new('RGB', (columns * width, rows * height), '#eee7df')
    for index, (im, focus) in enumerate(photos):
        tile = ImageOps.fit(im, (width, height), Image.Resampling.LANCZOS, centering=tuple(focus))
        cover.paste(tile, ((index % columns) * width, (index // columns) * height))

    # The article exists once, as paragraphs. Copy and plain fallback derive from it.
    css = '''*{box-sizing:border-box}body{margin:0;background:#f3eee8;color:#3d3936;font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif}.panel,main{max-width:700px;margin:18px auto;background:#fffdf9;padding:26px}h1{font-size:24px;line-height:1.65;text-align:center}h2{font-size:18px}p,li{line-height:1.9}p{overflow-wrap:anywhere}button{border:0;border-radius:6px;padding:12px 16px;background:#765747;color:white;cursor:pointer;font:inherit;font-size:14px}.actions{display:flex;gap:10px;flex-wrap:wrap}#status{font-size:13px;color:#765747}textarea{width:100%;min-height:250px;line-height:1.8;font:inherit}a{color:#765747}@media(max-width:500px){.panel,main{margin:0 auto 12px;padding:22px 20px}}'''
    script = '''
const article=document.getElementById('article');
const plain=()=>Array.from(article.children).map(p=>p.textContent).join('\\n\\n');
const status=document.getElementById('status');
function legacy(html,text){const handler=e=>{e.preventDefault();if(html)e.clipboardData.setData('text/html',html);e.clipboardData.setData('text/plain',text);};document.addEventListener('copy',handler);let ok=false;try{ok=document.execCommand('copy');}finally{document.removeEventListener('copy',handler);}return ok;}
async function copy(html,text){if(navigator.clipboard&&window.ClipboardItem){try{const data={'text/plain':new Blob([text],{type:'text/plain'})};if(html)data['text/html']=new Blob([html],{type:'text/html'});await navigator.clipboard.write([new ClipboardItem(data)]);return;}catch(e){}}if(!legacy(html,text))throw Error('copy unavailable');}
for(const id of ['title','body','comment'])document.getElementById('copy-'+id).addEventListener('click',async()=>{try{await copy(id==='body'?article.innerHTML:'',id==='body'?plain():document.getElementById(id).textContent);status.textContent='已复制。正文插图后只删除上传提示，保留图注。';}catch(e){status.textContent='复制未成功，请选中文字复制，或展开纯文字备用。';}});
document.getElementById('fallback').addEventListener('toggle',()=>{document.getElementById('plain').value=plain();});
'''
    alternatives = ''.join('<li>' + escape(t) + '</li>' for t in draft.get('alternatives', []))
    page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(draft['title']) + '</title><style>' + css + '</style></head><body>'
    page += '<section class="panel"><h2>她的出片日记 · 公众号粘贴版</h2><p>标题、正文、置顶评论分别复制。正文导语段后上传封面_整合图.jpg作为整合拼图，后续按提示上传images内对应照片；只删除上传提示，保留图注。设置封面时复用同一张拼图。</p><div class="actions"><button id="copy-title">复制标题</button><button id="copy-body">复制正文</button><button id="copy-comment">复制置顶评论</button></div><p id="status" role="status">拼图复用整合封面，独立配图使用images中的一套文件。</p><h1 id="title">' + escape(draft['title']) + '</h1>'
    if alternatives:
        page += '<details><summary>备选标题</summary><ol>' + alternatives + '</ol></details>'
    page += '</section><main><div id="article">' + ''.join(body) + '</div></main>'
    page += '<section class="panel"><h2>置顶评论草稿</h2><p id="comment">' + escape(draft['comment']) + '</p><p><a href="封面_整合图.jpg">打开整合封面</a></p><details id="fallback"><summary>纯文字备用</summary><textarea id="plain" readonly aria-label="纯文字备用正文"></textarea></details><p>本地复制页不替代公众号最终预览，上传后请核对排版。</p></section><script>' + script + '</script></body></html>'
    for cap in captions:
        if page.count(escape(cap)) != 1:
            raise ValueError('A caption must be unique and must not repeat body text: ' + cap)
    if page.count('【配图') != count:
        raise ValueError('Upload marker mismatch.')
    (output / '公众号粘贴版.html').write_text(page, encoding='utf-8')
    cover.save(output / '封面_整合图.jpg', quality=96, subsampling=0)
    photo_fields = {'file', 'media_url', 'post_image_position', 'pose', 'pose_observation', 'source_sha256', 'sha256', 'article_order', 'selected_preview_number', 'upload_file', 'caption'}
    common = {key: value for key, value in ordered[0].items() if key not in photo_fields and all(r.get(key) == value for r in ordered)}
    compact_sources = {'common': common, 'images': [{key: value for key, value in r.items() if key not in common} for r in ordered], 'cover': {'file': '封面_整合图.jpg', 'dimensions': list(cover.size), 'columns': columns, 'rows': rows, 'cell': [width, height], 'focus': [focus for _, focus in photos], 'article_position': 'after_first_paragraph', 'caption': collage_caption, 'accent': '#' + accent, 'accent_source': accent_source, 'typography': 'centred'}}
    (output / 'sources.json').write_text(json.dumps(compact_sources, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'directory': str(output), 'photos': count, 'cover': list(cover.size), 'outputs': ['公众号粘贴版.html', '封面_整合图.jpg', 'sources.json']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, help='Confirmed article directory with images/ and sources.json')
    parser.add_argument('--draft', required=True, help='Temporary editorial JSON, outside the delivery directory')
    arguments = parser.parse_args()
    build(arguments.directory, arguments.draft)
