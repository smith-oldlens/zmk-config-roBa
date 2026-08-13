#pragma once

#include <stdbool.h>
#include <zephyr/types.h>

#ifdef __cplusplus
extern "C" {
#endif

enum pmw3610_accel_preset {
    PMW3610_ACCEL_PRESET_OFF = 0,
    PMW3610_ACCEL_PRESET_WEAK,
    PMW3610_ACCEL_PRESET_MID,
    PMW3610_ACCEL_PRESET_STRONG,
    PMW3610_ACCEL_PRESET_COUNT,
};

struct pmw3610_runtime_config {
    uint16_t cpi;
    uint8_t accel_preset;
    uint16_t snipe_cpi;
    uint8_t snipe_divisor;
};

#define PMW3610_RUNTIME_CPI_STEP 50
#define PMW3610_RUNTIME_SNIPE_CPI_STEP 200
#define PMW3610_RUNTIME_SNIPE_DIVISOR_MIN 1
#define PMW3610_RUNTIME_SNIPE_DIVISOR_MAX 100

// カーソルCPIを delta だけ増減させる（200〜3200にクランプ）
void pmw3610_cpi_adjust(int32_t delta);

// 加速度プリセットを OFF -> 弱 -> 中 -> 強 -> OFF... と循環させる
void pmw3610_accel_cycle(void);

// USBライブ調整用。preview/defaults/discard はRAMだけを変更する。
void pmw3610_runtime_get_current(struct pmw3610_runtime_config *config);
void pmw3610_runtime_get_saved(struct pmw3610_runtime_config *config);
void pmw3610_runtime_get_defaults(struct pmw3610_runtime_config *config);
bool pmw3610_runtime_is_valid(const struct pmw3610_runtime_config *config);
bool pmw3610_runtime_is_dirty(void);
int pmw3610_runtime_set_preview(const struct pmw3610_runtime_config *config);
int pmw3610_runtime_save(const struct pmw3610_runtime_config *config);
void pmw3610_runtime_discard(void);
void pmw3610_runtime_use_defaults(void);

#ifdef __cplusplus
}
#endif
