cmake_minimum_required(VERSION 3.16)
set(PROJECT_VER "native-BLE-AB1-V2D-P1")
# Offline trial builds use the exact saved dependencies, without registry resolution.
if("$ENV{IDF_COMPONENT_MANAGER}" STREQUAL "0")
    list(APPEND EXTRA_COMPONENT_DIRS "../offline-components/cjson" "../offline-components/mqtt")
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(omk_node)
