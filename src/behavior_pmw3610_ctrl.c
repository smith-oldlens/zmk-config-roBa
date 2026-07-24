/*
 * トラックボールのカーソル速度・加速度プリセットを、押した瞬間にランタイムで
 * 変更するための簡易ビヘイビア。ビルド不要でキー操作だけで即反映される。
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <drivers/behavior.h>
#include <zmk/behavior.h>

#include "pmw3610_runtime.h"

// --- &cpi_adjust <delta> ---------------------------------------------------
#define DT_DRV_COMPAT zmk_behavior_cpi_adjust

static int cpi_adjust_pressed(struct zmk_behavior_binding *binding,
                               struct zmk_behavior_binding_event event) {
    pmw3610_cpi_adjust((int32_t)binding->param1);
    return ZMK_BEHAVIOR_OPAQUE;
}

static int cpi_adjust_released(struct zmk_behavior_binding *binding,
                                struct zmk_behavior_binding_event event) {
    return ZMK_BEHAVIOR_OPAQUE;
}

static const struct behavior_driver_api behavior_cpi_adjust_driver_api = {
    .binding_pressed = cpi_adjust_pressed,
    .binding_released = cpi_adjust_released,
};

#define CPI_ADJUST_INST(n)                                                                       \
    BEHAVIOR_DT_INST_DEFINE(n, NULL, NULL, NULL, NULL, POST_KERNEL,                               \
                             CONFIG_KERNEL_INIT_PRIORITY_DEFAULT,                                  \
                             &behavior_cpi_adjust_driver_api);

DT_INST_FOREACH_STATUS_OKAY(CPI_ADJUST_INST)

#undef DT_DRV_COMPAT

// --- &accel_cycle ------------------------------------------------------------
#define DT_DRV_COMPAT zmk_behavior_accel_cycle

static int accel_cycle_pressed(struct zmk_behavior_binding *binding,
                                struct zmk_behavior_binding_event event) {
    pmw3610_accel_cycle();
    return ZMK_BEHAVIOR_OPAQUE;
}

static int accel_cycle_released(struct zmk_behavior_binding *binding,
                                 struct zmk_behavior_binding_event event) {
    return ZMK_BEHAVIOR_OPAQUE;
}

static const struct behavior_driver_api behavior_accel_cycle_driver_api = {
    .binding_pressed = accel_cycle_pressed,
    .binding_released = accel_cycle_released,
};

#define ACCEL_CYCLE_INST(n)                                                                      \
    BEHAVIOR_DT_INST_DEFINE(n, NULL, NULL, NULL, NULL, POST_KERNEL,                               \
                             CONFIG_KERNEL_INIT_PRIORITY_DEFAULT,                                  \
                             &behavior_accel_cycle_driver_api);

DT_INST_FOREACH_STATUS_OKAY(ACCEL_CYCLE_INST)

#undef DT_DRV_COMPAT
