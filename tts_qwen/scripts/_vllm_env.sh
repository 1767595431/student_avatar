# sourced by start_vllm_gpu*.sh — cu13 入 LD_LIBRARY_PATH
# Qwen talker 的 SiluAndMul 会走 _C_stable_libtorch→libcudart.so.13；
# 本机驱动只到 12.8，故 deploy yaml 里 custom_ops=none + enforce_eager。
CU13_LIB="$(python - <<'PY'
import pathlib
p = pathlib.Path(__import__("nvidia").__path__[0]) / "cu13" / "lib"
print(p if p.is_dir() else "")
PY
)"
if [[ -n "$CU13_LIB" ]]; then
  export LD_LIBRARY_PATH="${CU13_LIB}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
unset CU13_LIB
