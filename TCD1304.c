#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "hardware/adc.h"
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/pio.h"
#include "pico/stdlib.h"
#include "tcd1304.pio.h"
#include "pico/stdio_usb.h"

#define MC_PIN 14
#define SH_PIN 9
#define ICG_PIN 13
#define ADC_PIN 26
#define ADC_CHANNEL 0

#define SYS_CLOCK_KHZ 200000u
#define MC_FREQUENCY_HZ 2000000u
#define SAMPLE_COUNT 3694u
#define FRAME_AVERAGES 10u
#define INTEGRATION_US 10000u

#define FRAME_MAGIC 0x34444354u /* "TCD4" on the wire, little-endian */
#define PROTOCOL_VERSION 2u

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t version;
    uint16_t header_bytes;
    uint32_t frame_number;
    uint16_t sample_count;
    uint16_t averages;
    uint32_t integration_us;
    uint32_t payload_bytes;
} frame_header_t;

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t length) {
    while (length--) {
        crc ^= *data++;
        for (uint bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return crc;
}

static void send_frame(const uint16_t *samples, uint32_t frame_number) {
    const frame_header_t header = {
        .magic = FRAME_MAGIC,
        .version = PROTOCOL_VERSION,
        .header_bytes = sizeof(frame_header_t),
        .frame_number = frame_number,
        .sample_count = SAMPLE_COUNT,
        .averages = FRAME_AVERAGES,
        .integration_us = INTEGRATION_US,
        .payload_bytes = sizeof(uint16_t) * SAMPLE_COUNT,
    };
    uint32_t crc = crc32_update(0xffffffffu, (const uint8_t *)&header,
                                sizeof(header));
    crc = crc32_update(crc, (const uint8_t *)samples,
                       sizeof(uint16_t) * SAMPLE_COUNT) ^ 0xffffffffu;
    fwrite(&header, 1, sizeof(header), stdout);
    fwrite(samples, sizeof(uint16_t), SAMPLE_COUNT, stdout);
    fwrite(&crc, 1, sizeof(crc), stdout);
    fflush(stdout);
}

static void capture_once(PIO pio, uint gate_sm, uint dma_channel,
                         const dma_channel_config *dma_config,
                         uint16_t *samples) {
    adc_fifo_drain();
    dma_channel_configure(dma_channel, dma_config, samples, &adc_hw->fifo,
                          SAMPLE_COUNT, true);
    adc_run(true);

    /* A FIFO word requests the PIO-timed ICG/SH sequence. */
    pio_sm_put_blocking(pio, gate_sm, 1u);
    dma_channel_wait_for_finish_blocking(dma_channel);

    adc_run(false);
    adc_fifo_drain();
}

int main(void) {
    set_sys_clock_khz(SYS_CLOCK_KHZ, true);
    stdio_init_all();
    stdio_set_translate_crlf(&stdio_usb, false);
    PIO pio = pio0;
    const uint mc_sm = pio_claim_unused_sm(pio, true);
    const uint gate_sm = pio_claim_unused_sm(pio, true);
    const uint mc_offset = pio_add_program(pio, &tcd1304_master_clock_program);
    const uint gate_offset = pio_add_program(pio, &tcd1304_gates_program);
    const float mc_clkdiv =
        (float)clock_get_hz(clk_sys) / (2.0f * (float)MC_FREQUENCY_HZ);

    tcd1304_master_clock_init(pio, mc_sm, mc_offset, MC_PIN, mc_clkdiv);
    tcd1304_gates_init(pio, gate_sm, gate_offset, SH_PIN, ICG_PIN);
    pio_enable_sm_mask_in_sync(pio, (1u << mc_sm) | (1u << gate_sm));

    adc_init();
    adc_gpio_init(ADC_PIN);
    adc_select_input(ADC_CHANNEL);
    adc_fifo_setup(true, true, 1, false, false);
    adc_set_clkdiv(0.0f);

    const uint dma_channel = dma_claim_unused_channel(true);
    dma_channel_config dma_config =
        dma_channel_get_default_config(dma_channel);
    channel_config_set_transfer_data_size(&dma_config, DMA_SIZE_16);
    channel_config_set_read_increment(&dma_config, false);
    channel_config_set_write_increment(&dma_config, true);
    channel_config_set_dreq(&dma_config, DREQ_ADC);

    static uint16_t capture[SAMPLE_COUNT];
    static uint16_t averaged[SAMPLE_COUNT];
    static uint32_t sums[SAMPLE_COUNT];
    uint32_t frame_number = 0;

    sleep_ms(1000);
    /* Prime the CCD once; subsequent SH edges define exact integration
       intervals. This discarded read also removes power-up contents. */
    capture_once(pio, gate_sm, dma_channel, &dma_config, capture);
    absolute_time_t next_shift = get_absolute_time();

    while (true) {
        memset(sums, 0, sizeof(sums));
        for (uint average = 0; average < FRAME_AVERAGES; ++average) {
            /* Schedule SH-to-SH, rather than adding the 7.4 ms readout time
               to the requested integration interval. */
            next_shift = delayed_by_us(next_shift, INTEGRATION_US);
            sleep_until(next_shift);
            capture_once(pio, gate_sm, dma_channel, &dma_config, capture);
            for (uint i = 0; i < SAMPLE_COUNT; ++i)
                sums[i] += capture[i];
        }
        for (uint i = 0; i < SAMPLE_COUNT; ++i)
            averaged[i] =
                (uint16_t)((sums[i] + FRAME_AVERAGES / 2u) / FRAME_AVERAGES);
        send_frame(averaged, frame_number++);
    }
}
