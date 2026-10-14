# Patch a build-local copy, never the component-manager cache. Fail closed if
# dependency content changes. Both configure AND incremental builds verify it.
idf_component_get_property(nd_mqtt_lib espressif__mqtt COMPONENT_LIB)
idf_component_get_property(nd_mqtt_dir espressif__mqtt COMPONENT_DIR)
set(nd_original "${nd_mqtt_dir}/mqtt_client.c")
set(nd_generated "${CMAKE_BINARY_DIR}/network_diagnostics/mqtt_client.c")
set(nd_script "${CMAKE_CURRENT_LIST_DIR}/../scripts/prepare_mqtt_diagnostics.py")
set(nd_patch "${CMAKE_CURRENT_LIST_DIR}/../patches/mqtt-1.1.0-diagnostics.patch")
set(nd_hashes "${CMAKE_CURRENT_LIST_DIR}/../patches/mqtt-1.1.0-diagnostics.json")
execute_process(COMMAND "${PYTHON}" "${nd_script}" "${nd_original}" "${nd_generated}"
                RESULT_VARIABLE nd_result)
if(NOT nd_result EQUAL 0)
    message(FATAL_ERROR "MQTT diagnostic patch verification failed")
endif()
add_custom_command(OUTPUT "${nd_generated}"
    COMMAND "${PYTHON}" "${nd_script}" "${nd_original}" "${nd_generated}"
    DEPENDS "${nd_original}" "${nd_script}" "${nd_patch}" "${nd_hashes}"
    VERBATIM)
add_custom_target(verify_network_diagnostics
    COMMAND "${PYTHON}" "${nd_script}" "${nd_original}" "${nd_generated}"
    DEPENDS "${nd_generated}" VERBATIM)
add_dependencies(${nd_mqtt_lib} verify_network_diagnostics)
get_target_property(nd_sources ${nd_mqtt_lib} SOURCES)
list(FIND nd_sources "${nd_original}" nd_source_index)
if(nd_source_index EQUAL -1)
    message(FATAL_ERROR "Expected ESP-MQTT compilation unit is missing")
endif()
list(REMOVE_ITEM nd_sources "${nd_original}")
list(APPEND nd_sources "${nd_generated}")
set_property(TARGET ${nd_mqtt_lib} PROPERTY SOURCES "${nd_sources}")
target_include_directories(${nd_mqtt_lib} PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../src")
if(OMK_NETWORK_DIAGNOSTICS)
    target_compile_definitions(${nd_mqtt_lib} PRIVATE OMK_NETWORK_DIAGNOSTICS=1)
endif()
