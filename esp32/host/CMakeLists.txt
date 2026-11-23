# Host build of the portable firmware modules: a display preview and unit tests.
#
#   cmake -S esp32/host -B esp32/host/build && cmake --build esp32/host/build
#   esp32/host/build/preview out.pgm      # every screen with made-up data
#   esp32/host/build/tests                # dispatcher / Firestore encoding / formatting
#
# The Firestore tests need cJSON; set IDF_PATH (ESP-IDF's copy is used) or
# install libcjson-dev.
cmake_minimum_required(VERSION 3.16)
project(huckdeck_host C)

set(CMAKE_C_STANDARD 11)
set(MAIN ${CMAKE_CURRENT_SOURCE_DIR}/../main)
add_compile_options(-Wall -Wextra -O1 -g -DHUCKDECK_HOST -D_GNU_SOURCE)

add_library(portable STATIC
    ${MAIN}/gfx.c ${MAIN}/display.c ${MAIN}/fonts/fonts_data.c
    platform_host.c)
target_include_directories(portable PUBLIC ${MAIN} ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(portable PUBLIC m)

add_executable(preview preview.c)
target_link_libraries(preview portable)

# cJSON for the Firestore encoding tests
if(DEFINED ENV{IDF_PATH} AND EXISTS $ENV{IDF_PATH}/components/json/cJSON/cJSON.c)
    set(CJSON_SRC $ENV{IDF_PATH}/components/json/cJSON/cJSON.c)
    set(CJSON_INC $ENV{IDF_PATH}/components/json/cJSON)
else()
    find_path(CJSON_INC cjson/cJSON.h)
    find_library(CJSON_LIB cjson)
endif()
if(CJSON_SRC OR CJSON_LIB)
    add_executable(tests tests.c ${MAIN}/dispatcher.c ${MAIN}/huck_api.c ${MAIN}/firebase_auth.c ${MAIN}/firestore.c ${CJSON_SRC})
    target_include_directories(tests PRIVATE ${CJSON_INC})
    target_link_libraries(tests portable ${CJSON_LIB})
else()
    message(WARNING "cJSON not found: skipping the tests target")
endif()
