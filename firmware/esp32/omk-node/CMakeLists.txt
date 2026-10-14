cmake_minimum_required(VERSION 3.16)
option(OMK_NETWORK_DIAGNOSTICS "Bounded Mesh/MQTT diagnostic trial" ON)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(omk_node)
# The bounded MQTT adapter is required even when diagnostic reporting is off.
include(cmake/network_diagnostics.cmake)
