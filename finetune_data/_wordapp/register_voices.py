# -*- coding: utf-8 -*-
"""이한(남)·리리(여) 중국어 녹음을 myvoice 목소리 목록에 등록한다.
   다섯 녹음 가운데 가장 고르게 말한 것을 대표(참조) 음성으로 삼는다.
   잣대: 무음이 적고 목청 울림이 고른 것 — 목소리 복제는 이 참조를 본뜬다."""
import os, glob, sqlite3
os.chdir('/app')
import numpy as np, soundfile as sf, librosa

SPK = {'이한': ('남', 'voice_samples/이한'), '리리': ('여', 'voice_samples/리리')}
con = sqlite3.connect('database/voices.db', timeout=60)

for name, (sex, d) in SPK.items():
    best = None
    for p in sorted(glob.glob(f'{d}/*.wav')):
        w, sr = sf.read(p)
        if w.ndim > 1: w = w.mean(axis=1)
        dur = len(w) / sr
        y = librosa.resample(w.astype(np.float32), orig_sr=sr, target_sr=16000)
        _, vo, _ = librosa.pyin(y, fmin=70, fmax=400, sr=16000, frame_length=1024)
        voiced = float(np.nanmean(vo))
        quiet  = float(np.mean(np.abs(w) < 0.008))
        sc = voiced * 2 - quiet * 2
        print(f"  {os.path.basename(p)} {dur:.1f}초 울림 {voiced:.2f} 무음 {quiet:.2f} → {sc:.2f}")
        if best is None or sc > best[0]: best = (sc, p, dur)
    _, ref, dur = best
    label = f"{name}({sex}·중국어)"
    row = con.execute("SELECT id FROM voices WHERE name=?", (label,)).fetchone()
    if row:
        con.execute("UPDATE voices SET file_path=?, duration=? WHERE id=?", (ref, dur, row[0]))
        print(f"{label} 고쳐 등록 (id {row[0]}) ← {os.path.basename(ref)}\n")
    else:
        cur = con.execute("""INSERT INTO voices(name,file_path,duration,user_id,folder_type,
                             status,can_generate,description)
                             VALUES(?,?,?,1,'personal','active',1,?)""",
                          (label, ref, dur, f'중국어 학습용 {sex}성 녹음 5편 (총 {sum(1 for _ in glob.glob(d+"/*.wav"))}개)'))
        print(f"{label} 새로 등록 (id {cur.lastrowid}) ← {os.path.basename(ref)}\n")
con.commit(); con.close()
