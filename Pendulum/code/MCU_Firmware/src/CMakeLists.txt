file(GLOB_RECURSE app_sources ${CMAKE_CURRENT_LIST_DIR}/*.c)
idf_component_register(SRCS ${app_sources} INCLUDE_DIRS "."
    REQUIRES driver esp_timer fatfs sdmmc vfs esp_adc esp_hw_support esp_wifi esp_netif esp_event nvs_flash mdns)