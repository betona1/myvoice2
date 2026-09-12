# -*- coding: utf-8 -*-
"""이한(남)·리리(여) 중국어 녹음으로 XTTS 파인튜닝.
   자료는 build_zh_dataset.py 가 미리 잘라 둔 것을 쓴다
   (코퀴 기본 자르개는 중국어에서 한 조각도 못 만든다 — 그쪽은 마침표에만 자른다).
   ⚠️ 언어 코드는 'zh' (합성 때 쓰는 'zh-cn' 아님)
   ⚠️ 10GB 카드 한 장 → 두 사람을 잇달아 학습"""
import os, sys, json, time, shutil, traceback
from pathlib import Path
sys.path.insert(0, '/app'); os.chdir('/app')
from tts.finetuner import run_finetune, FINETUNE_DIR

EPOCHS = int(os.environ.get('EPOCHS', '40'))
BATCH  = int(os.environ.get('BATCH',  '3'))
for name in ('이한', '리리'):
    ds = f'finetune_data/_zhds/{name}'
    if not os.path.exists(f'{ds}/metadata_train.csv'):
        print(f"{name}: 학습 자료가 없다 — 건너뜀"); continue
    n = sum(1 for _ in open(f'{ds}/metadata_train.csv', encoding='utf-8')) - 1
    stamp = time.strftime('%Y%m%d_%H%M%S')
    base = str(FINETUNE_DIR / f'{name}_zh_{stamp}')
    print(f"\n{'='*54}\n{name} 학습 시작 — 조각 {n}개, 에포크 {EPOCHS}, 배치 {BATCH}\n{'='*54}", flush=True)
    t0 = time.time()
    try:
        r = run_finetune(train_csv=f'{ds}/metadata_train.csv', eval_csv=f'{ds}/metadata_eval.csv',
                         output_path=base, language='zh', num_epochs=EPOCHS,
                         batch_size=BATCH, grad_acumm=int(os.environ.get('GRAD','3')))
        md = str(FINETUNE_DIR / f'{name}_zh_model'); os.makedirs(md, exist_ok=True)
        shutil.copy2(r['model_path'],  f'{md}/model.pth')
        shutil.copy2(r['config_path'], f'{md}/config.json')
        shutil.copy2(r['vocab_path'],  f'{md}/vocab.json')
        if r.get('speaker_ref'):
            shutil.copy2(r['speaker_ref'], f'{md}/reference.wav')
        meta = {'voice_name': f'{name}_zh', 'language': 'zh', 'segments': n,
                'num_epochs': EPOCHS, 'created_at': stamp,
                'model_path': f'{md}/model.pth', 'config_path': f'{md}/config.json',
                'vocab_path': f'{md}/vocab.json', 'speaker_ref': f'{md}/reference.wav'}
        json.dump(meta, open(f'{md}/meta.json', 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        print(f"\n{name} 학습 끝 ({(time.time()-t0)/60:.1f}분) → {md}", flush=True)
    except Exception:
        traceback.print_exc()
        print(f"\n{name} 학습 실패", flush=True)
