#!/usr/bin/env python3
"""TTS 배치 생성 — 모든 중국어 카드 (김준용 + 수민)"""
import sqlite3, sys, os, numpy as np, soundfile as sf, uuid, time
sys.path.insert(0, '/app')
os.chdir('/app')

from tts.engine import get_engine
from pathlib import Path
from main import preprocess_tts_text

engine = get_engine()
AUDIO_DIR = Path('outputs/chinese')
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

voice_8 = 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav'
voice_11 = 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'

SUBJECTS = ['기초중국��', '기초중국어2', '생활중국��', '중국어발음연습', '��음연습', '중국인의생활과문화']

def gen_tts(engine, card_id, text, ref_voice, col_name, conn):
    try:
        audio_path = str(AUDIO_DIR / f'{col_name}_{card_id}_{uuid.uuid4().hex[:6]}.wav')
        processed = preprocess_tts_text(text, 'zh-cn')
        wav_list = engine.tts.tts(text=processed, speaker_wav=ref_voice, language='zh-cn')
        wav = np.array(wav_list, dtype=np.float32)
        sf.write(audio_path, wav, 24000)
        c = conn.cursor()
        c.execute(f'UPDATE chinese_cards SET {col_name} = ? WHERE id = ?', (audio_path, card_id))
        return True
    except Exception as e:
        print(f'  에러: {text[:20]} — {e}')
        return False

for subject in SUBJECTS:
    conn = sqlite3.connect('database/voices.db', timeout=30)
    c = conn.cursor()

    # 김준용
    c.execute("""SELECT id, chinese FROM chinese_cards
                 WHERE subject=? AND (ref_audio_path IS NULL OR ref_audio_path = '')
                 AND chinese IS NOT NULL AND length(chinese) >= 1""", (subject,))
    cards_1 = c.fetchall()

    # 수��
    c.execute("""SELECT id, chinese FROM chinese_cards
                 WHERE subject=? AND (ref_audio_path_2 IS NULL OR ref_audio_path_2 = '')
                 AND chinese IS NOT NULL AND length(chinese) >= 1""", (subject,))
    cards_2 = c.fetchall()

    print(f'\n=== {subject} === 김준용:{len(cards_1)} 수민:{len(cards_2)}')

    done = 0
    for cid, text in cards_1:
        if gen_tts(engine, cid, text, voice_8, 'ref_audio_path', conn):
            done += 1
        if done % 20 == 0 and done > 0:
            conn.commit()
            print(f'  김준용 {done}/{len(cards_1)}')
    conn.commit()
    print(f'  김준용 완��: {done}/{len(cards_1)}')

    done2 = 0
    for cid, text in cards_2:
        if gen_tts(engine, cid, text, voice_11, 'ref_audio_path_2', conn):
            done2 += 1
        if done2 % 20 == 0 and done2 > 0:
            conn.commit()
            print(f'  수민 {done2}/{len(cards_2)}')
    conn.commit()
    print(f'  수민 완료: {done2}/{len(cards_2)}')
    conn.close()

print('\n전체 TTS 배치 완료!')
