"""GPU 2에 격리해서 XTTS 학습만 도는 서브프로세스 entrypoint."""
import sys, json, os, traceback
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

def main():
    payload_path = sys.argv[1]
    result_path = sys.argv[2]
    with open(payload_path, "r", encoding="utf-8") as f:
        args = json.load(f)
    try:
        from TTS.demos.xtts_ft_demo.utils.gpt_train import train_gpt
        config_path, xtts_checkpoint, vocab_path, trainer_out_path, speaker_ref = train_gpt(**args)
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump({
                "ok": True,
                "config_path": str(config_path),
                "xtts_checkpoint": str(xtts_checkpoint),
                "vocab_path": str(vocab_path),
                "trainer_out_path": str(trainer_out_path),
                "speaker_ref": str(speaker_ref),
            }, f)
    except Exception as e:
        traceback.print_exc()
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump({"ok": False, "error": f"{type(e).__name__}: {e}"}, f)
        sys.exit(1)

if __name__ == "__main__":
    main()
