cmake_minimum_required(VERSION 3.20)
project(protowire_python LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

# Same dance as protowire-cpp/CMakeLists.txt: when an old protobuf install
# leaks into /usr/local/include, force /opt/homebrew/include first so
# Homebrew's headers win the search-path race.
if(APPLE AND EXISTS /opt/homebrew/include AND EXISTS /usr/local/include/google/protobuf)
  add_compile_options(-I/opt/homebrew/include)
  message(STATUS "Detected stale /usr/local/include/google/protobuf — "
                 "prepending /opt/homebrew/include to compile search path.")
endif()
set(CMAKE_NO_SYSTEM_FROM_IMPORTED ON)

# Locate the sibling C++ library checkout. Default name is `protowire-cpp`;
# the legacy `protowire4cpp` name is also accepted as a fallback. Override
# with PROTOWIRE_CPP_DIR (preferred) or PROTOWIRE4CPP_DIR (legacy).
if(DEFINED ENV{PROTOWIRE_CPP_DIR})
  set(PROTOWIRE_CPP_DIR "$ENV{PROTOWIRE_CPP_DIR}" CACHE PATH "" FORCE)
elseif(DEFINED ENV{PROTOWIRE4CPP_DIR})
  set(PROTOWIRE_CPP_DIR "$ENV{PROTOWIRE4CPP_DIR}" CACHE PATH "" FORCE)
endif()
if(NOT DEFINED PROTOWIRE_CPP_DIR OR PROTOWIRE_CPP_DIR STREQUAL "")
  get_filename_component(_dir_dash
    "${CMAKE_CURRENT_SOURCE_DIR}/../protowire-cpp" ABSOLUTE)
  get_filename_component(_dir_legacy
    "${CMAKE_CURRENT_SOURCE_DIR}/../protowire4cpp" ABSOLUTE)
  if(EXISTS "${_dir_dash}/CMakeLists.txt")
    set(PROTOWIRE_CPP_DIR "${_dir_dash}" CACHE PATH "")
  else()
    set(PROTOWIRE_CPP_DIR "${_dir_legacy}" CACHE PATH "")
  endif()
endif()

if(NOT EXISTS "${PROTOWIRE_CPP_DIR}/CMakeLists.txt")
  message(FATAL_ERROR
    "protowire C++ library not found at ${PROTOWIRE_CPP_DIR}. "
    "Set PROTOWIRE_CPP_DIR (env var or -DPROTOWIRE_CPP_DIR=...).")
endif()

# Suppress test builds in the C++ subproject — we only want the libraries.
set(PROTOWIRE_BUILD_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory("${PROTOWIRE_CPP_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/protowire_cpp_build"
                 EXCLUDE_FROM_ALL)

# nanobind provides its own find module via the installed Python wheel.
find_package(Python 3.10 COMPONENTS Interpreter Development.Module REQUIRED)
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(_protowire NB_STATIC src/_protowire/module.cc)

# All the work is delegated to the C++ libraries.
target_link_libraries(_protowire PRIVATE protowire_pxf protowire_sbe)

install(TARGETS _protowire LIBRARY DESTINATION protowire)
