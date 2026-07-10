idf_component_register(
    SRCS
        "main.c"
        "camera_capture.c"
        "roi_diff.c"
        "inference.cc"
        "model_data.cc"
        "detection_agg.c"
        "soil_sensor.c"
        "decision_engine.c"
        "actuation.c"
        "lora_telemetry.c"
        "power_mgr.c"
        "event_log.c"
    INCLUDE_DIRS
        "."
    REQUIRES
        driver
        esp_adc
        esp_timer
        nvs_flash
        spiffs
        esp_hw_support
    PRIV_REQUIRES
        esp_system
)

# The radio component is intentionally optional for bench builds. If the
# documented firmware/components/ttn-esp32 checkout exists, expose its header
# and link it so lora_telemetry.c selects the real implementation instead of
# silently remaining on the logging stub.
idf_component_optional_requires(PRIVATE ttn-esp32)
