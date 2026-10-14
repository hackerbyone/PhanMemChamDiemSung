cmake_minimum_required(VERSION 3.16)
project(edge_qt LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets Network Sql)

add_executable(edge_qt
    src/main.cpp
    src/EdgeConfig.h
    src/NamePool.h
    src/VnLabels.h
    src/TargetRenderer.h
    src/TargetRenderer.cpp
    src/SyncClient.h
    src/SyncClient.cpp
    src/EdgeDatabase.h
    src/EdgeDatabase.cpp
    src/EdgeWorker.h
    src/EdgeWorker.cpp
    src/MainWindow.h
    src/MainWindow.cpp
    src/MainWindow.ui
)

target_link_libraries(edge_qt PRIVATE Qt6::Widgets Qt6::Network Qt6::Sql)

# The schema is NOT embedded as a Qt resource: a .qrc entry pointing outside
# its own directory (our shared ../schema.sql) makes CMake's AUTORCC
# dependency scanner (rcc --list) fail on some CMake/Ninja combinations --
# exactly the "rcc list process failed" error this project hit. Instead,
# EdgeDatabase::findSchemaPath() looks for schema.sql on disk at runtime
# (next to the .exe, or a few folders above it), and this compile-time
# absolute path is baked in as a last-resort fallback -- handy if the .exe
# ever gets copied somewhere without schema.sql alongside it.
target_compile_definitions(edge_qt PRIVATE "SCHEMA_SQL_SOURCE_PATH=\"${CMAKE_CURRENT_SOURCE_DIR}/../schema.sql\"")

# Vietnamese shooter names / UI labels are written as explicit \xHH UTF-8
# byte escapes in the source (see NamePool.h / VnLabels.h) specifically so
# they decode correctly with no compiler-specific flag needed -- but /utf-8
# is harmless and recommended on MSVC anyway for any other string literals
# in the codebase, so it's set here for safety.
if (MSVC)
    target_compile_options(edge_qt PRIVATE /utf-8)
endif()
