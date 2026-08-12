/*
 * roBa PMW3610 USB live adjustment transport.
 *
 * This deliberately uses a CDC ACM interface separate from ZMK Studio RPC.
 * Preview commands only touch RAM; COMMIT is the sole flash-writing command.
 */

#include <errno.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zmk/event_manager.h>
#include <zmk/events/position_state_changed.h>
#include <zmk/keymap.h>

#include "pmw3610_runtime.h"

LOG_MODULE_REGISTER(pmw3610_live_adjust, CONFIG_INPUT_LOG_LEVEL);

#define LIVE_UART_NODE DT_CHOSEN(roba_live_adjust_uart)
#define LIVE_PROTOCOL_VERSION 1
#define LIVE_COMMAND_MAX 96
#define LIVE_RESPONSE_MAX 240

BUILD_ASSERT(DT_HAS_CHOSEN(roba_live_adjust_uart),
             "CONFIG_PMW3610_LIVE_ADJUST requires roba,live-adjust-uart");

static const struct device *const live_uart = DEVICE_DT_GET(LIVE_UART_NODE);

struct live_command {
    char text[LIVE_COMMAND_MAX];
};

K_MSGQ_DEFINE(live_command_queue, sizeof(struct live_command), 4, 4);

static struct k_spinlock state_lock;
static uint64_t pressed_positions;

static char rx_line[LIVE_COMMAND_MAX];
static size_t rx_len;
static bool rx_overflow;

static void live_send(const char *response) {
    for (const char *p = response; *p; p++) {
        // Zephyrのpolling UART APIは送信失敗値を返さない。次の要求の
        // タイムアウトと再ハンドシェイクで切断を検出する。
        (void)uart_poll_out(live_uart, (unsigned char)*p);
    }
    (void)uart_poll_out(live_uart, '\n');
}

static void live_send_error(const char *code, const char *detail) {
    char response[LIVE_RESPONSE_MAX];
    snprintf(response, sizeof(response), "ROBA1 ERR CODE=%s DETAIL=%s", code, detail);
    live_send(response);
}

static uint64_t live_pressed_snapshot(void) {
    k_spinlock_key_t key = k_spin_lock(&state_lock);
    uint64_t snapshot = pressed_positions;
    k_spin_unlock(&state_lock, key);
    return snapshot;
}

static void live_append_input_state(char *response, size_t response_size, size_t used) {
    uint64_t pressed = live_pressed_snapshot();
    snprintf(response + used, response_size - used,
             " LAYER=%u PRESSED_HI=%08X PRESSED_LO=%08X",
             (unsigned int)zmk_keymap_highest_layer_active(),
             (unsigned int)(pressed >> 32), (unsigned int)pressed);
}

static void live_send_status(void) {
    struct pmw3610_runtime_config current;
    struct pmw3610_runtime_config saved;
    struct pmw3610_runtime_config defaults;
    pmw3610_runtime_get_current(&current);
    pmw3610_runtime_get_saved(&saved);
    pmw3610_runtime_get_defaults(&defaults);

    char response[LIVE_RESPONSE_MAX];
    int used = snprintf(response, sizeof(response),
                        "ROBA1 OK PROTOCOL=%d DEVICE=roBa CPI=%u ACCEL=%u "
                        "SAVED_CPI=%u SAVED_ACCEL=%u DEFAULT_CPI=%u DEFAULT_ACCEL=%u DIRTY=%u",
                        LIVE_PROTOCOL_VERSION, current.cpi, current.accel_preset, saved.cpi,
                        saved.accel_preset, defaults.cpi, defaults.accel_preset,
                        pmw3610_runtime_is_dirty() ? 1 : 0);
    if (used > 0 && (size_t)used < sizeof(response)) {
        live_append_input_state(response, sizeof(response), (size_t)used);
    }
    live_send(response);
}

static bool live_parse_values(const char *text, const char *verb,
                              struct pmw3610_runtime_config *config) {
    unsigned int cpi;
    unsigned int accel;
    char extra;
    char format[48];
    snprintf(format, sizeof(format), "ROBA1 %s CPI=%%u ACCEL=%%u %%c", verb);
    int matched = sscanf(text, format, &cpi, &accel, &extra);
    if (matched != 2 || cpi > UINT16_MAX || accel > UINT8_MAX) {
        return false;
    }
    config->cpi = (uint16_t)cpi;
    config->accel_preset = (uint8_t)accel;
    return true;
}

static void live_handle_command(const char *command) {
    if (strcmp(command, "ROBA1 HELLO") == 0 || strcmp(command, "ROBA1 GET") == 0) {
        live_send_status();
        return;
    }
    if (strcmp(command, "ROBA1 DISCARD") == 0) {
        pmw3610_runtime_discard();
        live_send_status();
        return;
    }
    if (strcmp(command, "ROBA1 DEFAULTS") == 0) {
        pmw3610_runtime_use_defaults();
        live_send_status();
        return;
    }

    struct pmw3610_runtime_config config;
    if (strncmp(command, "ROBA1 PREVIEW ", 14) == 0) {
        if (!live_parse_values(command, "PREVIEW", &config)) {
            live_send_error("BAD_COMMAND", "expected_CPI_and_ACCEL");
            return;
        }
        int rc = pmw3610_runtime_set_preview(&config);
        if (rc < 0) {
            live_send_error("RANGE", "CPI_200_to_3200_step_50_ACCEL_0_to_3");
            return;
        }
        live_send_status();
        return;
    }
    if (strncmp(command, "ROBA1 COMMIT ", 13) == 0) {
        if (!live_parse_values(command, "COMMIT", &config)) {
            live_send_error("BAD_COMMAND", "expected_CPI_and_ACCEL");
            return;
        }
        int rc = pmw3610_runtime_save(&config);
        if (rc == -EINVAL) {
            live_send_error("RANGE", "CPI_200_to_3200_step_50_ACCEL_0_to_3");
            return;
        }
        if (rc < 0) {
            live_send_error("SAVE_FAILED", "settings_write_failed");
            return;
        }
        live_send_status();
        return;
    }
    live_send_error("BAD_COMMAND", "unknown_command");
}

static int live_input_event_listener(const zmk_event_t *eh) {
    const struct zmk_position_state_changed *position = as_zmk_position_state_changed(eh);
    if (position && position->position < 64) {
        k_spinlock_key_t key = k_spin_lock(&state_lock);
        uint64_t bit = UINT64_C(1) << position->position;
        if (position->state) {
            pressed_positions |= bit;
        } else {
            pressed_positions &= ~bit;
        }
        k_spin_unlock(&state_lock, key);
    }

    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(pmw3610_live_input, live_input_event_listener);
ZMK_SUBSCRIPTION(pmw3610_live_input, zmk_position_state_changed);

static void live_serial_callback(const struct device *dev, void *user_data) {
    ARG_UNUSED(user_data);
    if (!uart_irq_update(dev) || !uart_irq_rx_ready(dev)) {
        return;
    }

    uint8_t buffer[24];
    int count;
    while ((count = uart_fifo_read(dev, buffer, sizeof(buffer))) > 0) {
        for (int i = 0; i < count; i++) {
            char ch = (char)buffer[i];
            if (ch == '\r') {
                continue;
            }
            if (ch == '\n') {
                struct live_command queued = {0};
                if (rx_overflow) {
                    strncpy(queued.text, "ROBA1 INVALID_OVERFLOW", sizeof(queued.text) - 1);
                } else if (rx_len > 0) {
                    memcpy(queued.text, rx_line, rx_len);
                    queued.text[rx_len] = '\0';
                }
                if (queued.text[0] != '\0' &&
                    k_msgq_put(&live_command_queue, &queued, K_NO_WAIT) < 0) {
                    LOG_WRN("USB live command queue full");
                }
                rx_len = 0;
                rx_overflow = false;
                continue;
            }
            if (rx_overflow) {
                continue;
            }
            if (rx_len + 1 >= sizeof(rx_line)) {
                rx_overflow = true;
                continue;
            }
            rx_line[rx_len++] = ch;
        }
    }
}

static void live_adjust_thread(void) {
    struct live_command command;
    for (;;) {
        int rc = k_msgq_get(&live_command_queue, &command, K_FOREVER);
        if (rc < 0) {
            LOG_WRN("USB live queue read failed: %d", rc);
            continue;
        }
        live_handle_command(command.text);
    }
}

K_THREAD_DEFINE(pmw3610_live_adjust_thread, CONFIG_PMW3610_LIVE_ADJUST_THREAD_STACK_SIZE,
                live_adjust_thread, NULL, NULL, NULL,
                CONFIG_PMW3610_LIVE_ADJUST_THREAD_PRIORITY, 0, 0);

static int live_adjust_init(void) {
    if (!device_is_ready(live_uart)) {
        LOG_ERR("USB live adjustment UART is not ready");
        return -ENODEV;
    }
    int rc = uart_irq_callback_user_data_set(live_uart, live_serial_callback, NULL);
    if (rc < 0) {
        LOG_ERR("USB live UART callback setup failed: %d", rc);
        return rc;
    }
    uart_irq_rx_enable(live_uart);
    return 0;
}

SYS_INIT(live_adjust_init, POST_KERNEL, CONFIG_KERNEL_INIT_PRIORITY_DEFAULT);
