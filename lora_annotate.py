"""LoRA 清单标注工具：扫描 models/loras 下所有 safetensors，提取 metadata 生成标注文档。
用法: python lora_annotate.py [LORA_ROOT] [OUT_MD]
环境变量: LORA_ROOT（默认 ./models/loras）
"""
import io, sys, json, struct, os
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

LORA_ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ.get('LORA_ROOT', 'models/loras'))
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('LORAS.md')

# C 站来源信息（作者、版本 ID）——手工维护，下载时补充
# key = 本地文件名（相对 loras 根）；格式示例：
# KNOWN = {
#     'xxx.safetensors': {'author': '作者', 'civitai': 'modelId/versionId',
#                         'trigger': '触发词', 'func': '功能描述'},
# }
KNOWN = {}


def read_meta(path):
    try:
        with open(path, 'rb') as fp:
            n = struct.unpack('<Q', fp.read(8))[0]
            header = json.loads(fp.read(n))
        return header.get('__metadata__', {})
    except Exception as e:
        return {'_err': str(e)}


def main():
    lines = []
    lines.append('# LoRA 本地清单与标注\n')
    lines.append('> 生成时间：%s\n' % __import__('time').strftime('%Y-%m-%d %H:%M'))
    lines.append('> 标准程序：下载 LoRA 后 → 运行 `python lora_annotate.py` 刷新本清单 → 补充 C 站来源/触发词/功能\n')
    lines.append('## 清单\n')
    lines.append('| 文件名 | 作者 | 来源(model/ver) | 触发词 | 功能 | metadata 要点 |')
    lines.append('|---|---|---|---|---|---|')

    files = sorted(LORA_ROOT.rglob('*.safetensors'))
    for f in files:
        rel = f.relative_to(LORA_ROOT).as_posix()
        meta = read_meta(f)
        known = KNOWN.get(rel, {})
        author = known.get('author', meta.get('ss_creator', meta.get('author', '')) or '?')
        source = known.get('civitai', '')
        trigger = known.get('trigger', '')
        # 从 metadata 提取触发词（常见 key）
        for k in ('ss_tag_frequency', 'ss_output_name', 'trigger_word'):
            if k in meta and not trigger:
                v = str(meta[k])
                if k == 'trigger_word':
                    trigger = v
        func = known.get('func', '')
        meta_pts = []
        for k in ('ss_epoch', 'ss_num_train_images', 'ss_sd_model_name', 'training_steps', 'base_model'):
            if k in meta:
                meta_pts.append(f'{k}={str(meta[k])[:40]}')
        if '_err' in meta:
            meta_pts.append('ERR:' + meta['_err'][:40])
        meta_s = '; '.join(meta_pts)
        lines.append(f'| `{rel}` | {author} | {source} | {trigger} | {func} | {meta_s} |')

    text = '\n'.join(lines) + '\n'
    OUT.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
