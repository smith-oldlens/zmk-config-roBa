#pragma once

#include <zephyr/types.h>

#ifdef __cplusplus
extern "C" {
#endif

// カーソルCPIを delta だけ増減させる（200〜3200にクランプ、電源断でconfのデフォルトに戻る）
void pmw3610_cpi_adjust(int32_t delta);

// 加速度プリセットを OFF -> 弱 -> 中 -> 強 -> OFF... と循環させる
void pmw3610_accel_cycle(void);

#ifdef __cplusplus
}
#endif
