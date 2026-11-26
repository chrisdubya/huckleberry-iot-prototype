idf_component_register(
    SRCS "main.c" "wifi.c" "platform_esp.c" "http_esp.c" "firebase_auth.c" "firestore.c" "huck_api.c"
         "dispatcher.c" "buttons.c" "led.c" "ssd1322.c" "display.c" "gfx.c" "fonts/fonts_data.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES json esp_http_client esp-tls mbedtls nvs_flash esp_wifi esp_netif esp_event esp_timer driver esp_driver_gpio esp_driver_spi esp_driver_ledc esp_driver_rmt)
