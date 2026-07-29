# == DO NOT EDIT THE FOLLOWING LINES for the Raspberry Pi Pico VS Code Extension to work ==
if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.3.0)
set(toolchainVersion 14_2_Rel1)
set(picotoolVersion 2.3.0)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()
# ====================================================================================
# Generated Cmake Pico project file

cmake_minimum_required(VERSION 3.13)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Initialise pico_sdk from installed location
# (note this can come from environment, CMake cache etc)
set(PICO_BOARD pico CACHE STRING "Board type")

# Pull in Raspberry Pi Pico SDK (must be before project)
include(pico_sdk_import.cmake)

if (PICO_SDK_VERSION_STRING VERSION_LESS "1.4.0")
  message(FATAL_ERROR "Raspberry Pi Pico SDK version 1.4.0 (or later) required. Your version is ${PICO_SDK_VERSION_STRING}")
endif()

project(TCD1304 C CXX ASM)

# Initialise the Raspberry Pi Pico SDK
pico_sdk_init()

# Add executable. Default name is the project name, version 0.1

add_executable(TCD1304 TCD1304.c)

pico_set_program_name(TCD1304 "TCD1304")
pico_set_program_version(TCD1304 "0.1")

# Generate the synchronized TCD1304 clock/gate programs.
pico_generate_pio_header(TCD1304 ${CMAKE_CURRENT_LIST_DIR}/tcd1304.pio)

# Modify the below lines to enable/disable output over UART/USB
pico_enable_stdio_uart(TCD1304 0)
pico_enable_stdio_usb(TCD1304 1)

# Add the standard library to the build
target_link_libraries(TCD1304
        pico_stdlib
        hardware_adc
        hardware_dma
        # For the dummy output:
        hardware_pio
        pico_multicore
        hardware_timer
        hardware_clocks
        hardware_pwm
        )

# Add the standard include files to the build
target_include_directories(TCD1304 PRIVATE
  ${CMAKE_CURRENT_LIST_DIR}
  ${CMAKE_CURRENT_LIST_DIR}/.. # for our common lwipopts or any other standard includes, if required
)

pico_add_extra_outputs(TCD1304)

