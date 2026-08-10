"""LoRA 精确版系统搜索工具
========================
输入目标 LoRA 文件名（支持多个），自动穷举搜索 civitai.red：
  1. 多关键词变体（完整文件名保留 @/_/--- 特殊字符）
  2. 使用网页搜索端点 search-new.civitai.com/multi-search（models_v9 索引）
     ——比 API 搜索（/api/v1/models?query=）全：API 搜不到的模型（如
     surtr945 2692601、Hentai Studio Quality 1459030）网页搜索能搜到！
  3. @ 触发词文件名 → trainedWords 精确匹配；普通名 → 查详情精确匹配文件名
已知限制（来自官方文档 developer.civitai.com）：
  - 搜索用 query 参数时必须用 cursor 分页（page+query 组合返回 400）。
  - 429/5xx 指数退避重试（1s 起 30s 封顶），4xx 不重试（429 除外）。

--hash 模式：算本地文件 SHA256，调 /model-versions/by-hash 反查精确来源
（比穷举搜索可靠得多，未收录索引的版本也能查到）。
用法：
  python lora_search.py "ushikani_kassen_lora-000013.safetensors" "surtr945_v1.safetensors" [...]
  python lora_search.py --hash "models/loras/xxx.safetensors"
"""
import io, sys, json, time, re
from pathlib import Path
import httpx

import os
TOKEN = os.environ.get('CIVITAI_TOKEN', '')
BASE = 'https://civitai.red/api/v1/models'
SEARCH_KEY = os.environ.get('CIVITAI_SEARCH_KEY', '')
SEARCH_URL = 'https://search-new.civitai.com/multi-search'
c = httpx.Client(timeout=30, trust_env=False, follow_redirects=True)

# 已确认的精确版（避免重复找）
KNOWN_EXACT = {
    'ushikani_kassen_lora-000013.safetensors': ('2760349', '3106457', 'zhihu'),
    'anima-darklight-style-v1-000194.safetensors': ('2765580', '3112882', 'O_oo_O'),
    'RealSkin SliderV2.safetensors': ('2682590', '3068784', 'JIngGGYIIII'),
    'surtr945_v1.safetensors': ('2692601', '3023314', 'umina'),
    'anima-base-1-photo-background-v4.safetensors': ('1252497', '2959007', 'motimalu'),
}


def norm(s):
    s = re.sub(r'\.safetensors$', '', s)
    s = re.sub(r'^@', '', s)
    return re.sub(r'[^a-z0-9]+', '', s.lower())


def exact_name(s):
    """严格精确名：去 .safetensors 后缀和 @ 前缀，其余一字不差（区分大小写）"""
    s = re.sub(r'\.safetensors$', '', s)
    return re.sub(r'^@', '', s)


def api_get(path, params=None):
    """带过载重试的 GET"""
    for attempt in range(5):
        try:
            url = f'{BASE}/{path}' if path else BASE
            r = c.get(url, params=params, headers={'Authorization': f'Bearer {TOKEN}'})
            d = r.json()
            if isinstance(d, dict) and d.get('error'):
                if 'overload' in str(d.get('error')):
                    wait = 5 * (attempt + 1)
                    print(f'  (API 过载，{wait}s 后重试...)')
                    time.sleep(wait)
                    continue
                return None
            return d
        except Exception as e:
            time.sleep(3)
    return None


def search(query, limit=50):
    """网页搜索端点（models_v9 索引，比 API 搜索全——API 搜不到的 publishedAt=None 模型这里能搜到）"""
    for attempt in range(3):
        try:
            body = {'queries': [{'q': query, 'indexUid': 'models_v9', 'limit': limit, 'offset': 0}]}
            r = c.post(SEARCH_URL, json=body,
                       headers={'Authorization': f'Bearer {SEARCH_KEY}', 'Content-Type': 'application/json'})
            d = r.json()
            hits = d.get('results', [{}])[0].get('hits', [])
            return [h for h in hits if h.get('type') == 'LORA']
        except Exception:
            time.sleep(3)
    return []


def model_detail(mid):
    return api_get(str(mid))


def gen_queries(filename):
    """生成多关键词变体（保留 @、_、--- 等特殊字符——直接搜完整文件名）"""
    core = re.sub(r'\.safetensors$', '', filename)
    qs = set()
    qs.add(core)                            # 完整文件名（保留 @ _ ---）
    qs.add(re.sub(r'-(step)?\d+$', '', core))  # 去 step/epoch 数字后缀
    qs.add(re.sub(r'[_-]+', ' ', core).strip())  # 分隔符转空格
    if core.startswith('@'):
        qs.add(core.split('_')[0])          # 纯触发词主体（@a1g2m3）
        qs.add(re.sub(r'^@', '', core.split('_')[0]))  # 去 @ 的触发词
    # 去 anima-base-1 前缀（模型名通常不含它）
    m = re.match(r'^(?:anima[-_ ]?base[-_ ]?1[-_ ]?)?(.*)$', core, flags=re.I)
    if m.group(1) != core:
        qs.add(m.group(1))
        qs.add(re.sub(r'[_-]+', ' ', m.group(1)).strip())
    # 去 v 数字后缀（skintextureV1 -> skintexture）
    qs.add(re.sub(r'[_ ]?v\d[\d.]*$', '', core, flags=re.I))
    return [q for q in qs if q]


def find_exact_for(filename):
    if filename in KNOWN_EXACT:
        mid, vid, author = KNOWN_EXACT[filename]
        print(f'  [已知] {filename} -> {mid}/{vid} ({author})')
        return True

    queries = gen_queries(filename)
    seen = {}  # mid -> hit

    for q in queries:
        for h in search(q):
            seen[str(h.get('id'))] = h

    print(f'  搜索到 {len(seen)} 个候选模型')

    # 先查详情（含 files），做文件名精确匹配；详情缓存供触发词排序用
    details = {}
    for mid in list(seen)[:20]:
        d = model_detail(mid)
        if d:
            details[mid] = d

    # 文件名匹配（最优先：Hentai_Studio_Quality / kedama-milk_V2.0_epoch45 这类完全一致）
    target = exact_name(filename)
    for mid, d in details.items():
        for v in d.get('modelVersions', []):
            for f in v.get('files', []):
                if f.get('type') != 'Model':
                    continue
                if exact_name(f['name']) == target:
                    print(f'  [EXACT] {filename} -> model={mid} ver={v["id"]} '
                          f'base={v.get("baseModel")} author={d.get("creator",{}).get("username","?")}')
                    return True

    # 触发词匹配：trainedWords 包含/前缀即精确（短词只用相等避免误报）
    core = re.sub(r'\.safetensors$', '', filename)
    trigger = core.lower()
    trigger_bare = re.sub(r'^@', '', trigger)
    target_is_anima = 'anima' in trigger_bare
    matches = []
    for mid, h in seen.items():
        for v in h.get('versions') or []:
            tw = [str(t).lower().strip(' ,').lstrip('@') for t in (v.get('trainedWords') or [])]
            hit = False
            tb_norm = norm(trigger_bare)
            for t in tw:
                tn = norm(t)
                if t == trigger_bare or tn == tb_norm:
                    hit = True
                elif len(tb_norm) >= 5 and (tb_norm.startswith(tn) or tn.startswith(tb_norm)):
                    hit = True
            if hit:
                # 指定 base_model 时排除不匹配的版本（避免其他 base 的触发词误报）
                if base_model:
                    b = (v.get('baseModel') or '').lower()
                    if base_model.lower() not in b:
                        continue
                matches.append((mid, v, h))
    if matches:
        trig_norm = norm(re.sub(r'^@', '', core))
        def key(m):
            mid, v, h = m
            d = details.get(mid) or {}
            files = [f for vv in d.get('modelVersions', []) for f in (vv.get('files') or []) if f.get('type') == 'Model']
            has_file = bool(files)
            score = 2 if has_file else 0
            # 文件名是目标名后缀/相等（如 kedama-milk_V2.0_epoch45 ⊂ @4x0style---kedama-milk_V2.0_epoch45）
            for f in files:
                fn = norm(f.get('name') or '')
                if fn and (fn == trig_norm or trig_norm.endswith(fn) or fn.endswith(trig_norm)):
                    score += 4
            base = (v.get('baseModel') or '')
            if target_is_anima and 'anima' in base.lower():
                score += 1
            if v.get('name') and trigger_bare in norm(v['name']):
                score += 1
            return -score
        mid, v, h = sorted(matches, key=key)[0]
        print(f'  [EXACT-触发词] {filename} -> model={mid} ver={v.get("id")} '
              f'base={v.get("baseModel")} author={(h.get("user") or {}).get("username","?")} '
              f'(trainedWords={v.get("trainedWords")})')
        return True

    # 近似候选输出
    print(f'  未找到精确版，近似候选:')
    target_core = norm(re.sub(r'[_-]+', '', re.sub(r'\.safetensors$', '', filename)))
    shown = 0
    for mid, h in seen.items():
        for v in (h.get('versions') or [])[:2]:
            if shown >= 10:
                break
            shown += 1
            print(f'    ~ model={mid} ver={v.get("id")} base={v.get("baseModel")} '
                  f'{h.get("name")} (author={(h.get("user") or {}).get("username","?")})')
    return False


def lookup_by_hash(path):
    """算本地文件 SHA256，用 /model-versions/by-hash 反查精确来源"""
    import hashlib
    print(f'\n=== 反查: {path} ===')
    h = hashlib.sha256()
    with open(path, 'rb') as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b''):
            h.update(chunk)
    sha = h.hexdigest().upper()
    print(f'  SHA256: {sha}')
    # by-hash 是 /api/v1/model-versions/ 下的兄弟路径，不走 models BASE
    try:
        r = c.get('https://civitai.red/api/v1/model-versions/by-hash/' + sha,
                  headers={'Authorization': f'Bearer {TOKEN}'})
        d = r.json()
    except Exception:
        d = None
    if not d or isinstance(d, dict) and d.get('error'):
        print('  未命中（404/错误）：', str(d)[:120])
        return False
    print(f'  [命中] modelId={d.get("modelId")} verId={d.get("id")} name={d.get("name")}')
    print(f'  base={d.get("baseModel")} status={d.get("status")} publishedAt={d.get("publishedAt")}')
    m = d.get('model') or {}
    print(f'  modelName={m.get("name")} type={m.get("type")} nsfw={m.get("nsfw")}')
    files = d.get('files') or []
    for f in files:
        if f.get('type') == 'Model':
            print(f'  文件: {f.get("name")} ({f.get("sizeKB", 0)/1024:.1f}MB)')
    return True


def find_exact_data(filename, fresh=False, base_model=None):
    """结构化搜索：返回 dict（不打印），供 CLI/MCP 使用
    {"exact": bool, "kind": "KNOWN|EXACT|EXACT-TRIGGER",
     "model_id", "version_id", "author", "base", "trained_words",
     "candidates": [...]}。
    base_model（如 'Anima'）来自工作流的 UNETLoader——命中时强制优先该 base，
    避免误匹配其他 base 的同名触发词 LoRA。"""
    saved = dict(KNOWN_EXACT)
    if fresh:
        KNOWN_EXACT.clear()
    result = {'exact': False, 'kind': None, 'model_id': None, 'version_id': None,
              'author': None, 'base': None, 'trained_words': None, 'candidates': []}
    try:
        if filename in KNOWN_EXACT:
            mid, vid, author = KNOWN_EXACT[filename]
            result.update(exact=True, kind='KNOWN', model_id=mid, version_id=vid, author=author)
            return result
        queries = gen_queries(filename)
        seen = {}
        for q in queries:
            for h in search(q):
                seen[str(h.get('id'))] = h
        details = {}
        for mid in list(seen)[:20]:
            d = model_detail(mid)
            if d:
                details[mid] = d
        target = exact_name(filename)
        for mid, d in details.items():
            for v in d.get('modelVersions', []):
                for f in v.get('files', []):
                    if f.get('type') == 'Model' and exact_name(f['name']) == target:
                        result.update(exact=True, kind='EXACT', model_id=mid, version_id=str(v['id']),
                                      author=d.get('creator', {}).get('username', '?'),
                                      base=v.get('baseModel'))
                        return result
        core = re.sub(r'\.safetensors$', '', filename)
        trigger_bare = re.sub(r'^@', '', core).lower()
        target_is_anima = 'anima' in trigger_bare
        matches = []
        for mid, h in seen.items():
            for v in h.get('versions') or []:
                tw = [str(t).lower().strip(' ,').lstrip('@') for t in (v.get('trainedWords') or [])]
                tb_norm = norm(trigger_bare)
                for t in tw:
                    tn = norm(t)
                    if t == trigger_bare or tn == tb_norm or (
                            len(tb_norm) >= 5 and (tb_norm.startswith(tn) or tn.startswith(tb_norm))):
                        if base_model:
                            b = (v.get('baseModel') or '').lower()
                            if base_model.lower() not in b:
                                break
                        matches.append((mid, v, h))
                        break
        if matches:
            trig_norm = norm(re.sub(r'^@', '', core))
            def key(m):
                mid, v, h = m
                d = details.get(mid) or {}
                files = [f for vv in d.get('modelVersions', []) for f in (vv.get('files') or []) if f.get('type') == 'Model']
                score = 2 if files else 0
                for f in files:
                    fn = norm(f.get('name') or '')
                    if fn and (fn == trig_norm or trig_norm.endswith(fn) or fn.endswith(trig_norm)):
                        score += 4
                base = (v.get('baseModel') or '')
                if base_model and 'anima' in base.lower():
                    score += 5
                elif target_is_anima and 'anima' in base.lower():
                    score += 1
                if base_model and not (base_model.lower() in base.lower()):
                    score -= 3
                return -score
            mid, v, h = sorted(matches, key=key)[0]
            result.update(exact=True, kind='EXACT-TRIGGER', model_id=mid, version_id=str(v.get('id')),
                          author=(h.get('user') or {}).get('username', '?'),
                          base=v.get('baseModel'), trained_words=v.get('trainedWords'))
            return result
        for mid, h in list(seen.items())[:10]:
            for v in (h.get('versions') or [])[:2]:
                result['candidates'].append({
                    'model_id': mid, 'name': h.get('name'),
                    'author': (h.get('user') or {}).get('username', '?'),
                    'base': v.get('baseModel'), 'version_id': str(v.get('id')),
                    'trained_words': v.get('trainedWords')})
        return result
    finally:
        KNOWN_EXACT.clear()
        KNOWN_EXACT.update(saved)


def main():
    if '--hash' in sys.argv:
        sys.argv.remove('--hash')
        paths = [a for a in sys.argv[1:] if a]
        if not paths:
            print('--hash 需要一个本地文件路径')
            sys.exit(1)
        for path in paths:
            lookup_by_hash(path)
        sys.exit(0)
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    fresh = '--fresh' in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)
    for filename in args:
        print(f'\n=== 搜索: {filename} ===')
        r = find_exact_data(filename, fresh=fresh)
        if r['exact']:
            print(f"  [{r['kind']}] {filename} -> model={r['model_id']} ver={r['version_id']} "
                  f"base={r['base']} author={r['author']}")
            if r['trained_words']:
                print(f'  (trainedWords={r["trained_words"]})')
        else:
            print('  未找到精确版，近似候选:')
            for c in r['candidates']:
                print(f"    ~ model={c['model_id']} ver={c['version_id']} base={c['base']} "
                      f"{c['name']} (author={c['author']})")


if __name__ == '__main__':
    main()
