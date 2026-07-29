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
#define EXPOSURE_US 10u

/* ADC starts just before the phase-anchored preflush pulse. At 500 ksps, the
   approximately 16 us through ICG rising produces 8 lead-in conversions before
   D0. Capturing
   these explicitly prevents the active spectrum from being shifted/truncated. */
#define ADC_LEAD_SAMPLES 8u
#define ADC_CAPTURE_COUNT (SAMPLE_COUNT + ADC_LEAD_SAMPLES)
#define READOUT_SHUTTER_PULSES 739u

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
        .integration_us = EXPOSURE_US,
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
                         uint16_t *capture) {
    adc_fifo_drain();
    dma_channel_configure(dma_channel, dma_config, capture, &adc_hw->fifo,
                          ADC_CAPTURE_COUNT, true);
    adc_run(true);

    /* Request one frame and provide the number of 40 us shutter cycles that
       keep the electronic shutter active throughout the line readout. */
    pio_sm_put_blocking(pio, gate_sm, 1u);
    pio_sm_put_blocking(pio, gate_sm, READOUT_SHUTTER_PULSES - 1u);
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
    const float gate_clkdiv =
        (float)clock_get_hz(clk_sys) / (2.0f * (float)MC_FREQUENCY_HZ);

    tcd1304_master_clock_init(pio, mc_sm, mc_offset, MC_PIN, mc_clkdiv);
    tcd1304_gates_init(pio, gate_sm, gate_offset, SH_PIN, ICG_PIN,
                       gate_clkdiv);
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

    static uint16_t capture[ADC_CAPTURE_COUNT];
    static uint16_t averaged[SAMPLE_COUNT];
    static uint32_t sums[SAMPLE_COUNT];
    uint32_t frame_number = 0;

    sleep_ms(1000);
    /* Discard one line to remove power-up contents and establish the 10 us
       electronic-shutter cadence. */
    capture_once(pio, gate_sm, dma_channel, &dma_config, capture);

    while (true) {
        memset(sums, 0, sizeof(sums));
        for (uint average = 0; average < FRAME_AVERAGES; ++average) {
            capture_once(pio, gate_sm, dma_channel, &dma_config, capture);
            for (uint i = 0; i < SAMPLE_COUNT; ++i)
                sums[i] += capture[i + ADC_LEAD_SAMPLES];
        }
        for (uint i = 0; i < SAMPLE_COUNT; ++i)
            averaged[i] =
                (uint16_t)((sums[i] + FRAME_AVERAGES / 2u) / FRAME_AVERAGES);
        send_frame(averaged, frame_number++);
    }
}
