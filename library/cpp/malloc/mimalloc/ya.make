LIBRARY()

NO_UTIL()
ALLOCATOR_IMPL()

PEERDIR(
    library/cpp/malloc/api
    contrib/libs/mimalloc
)

SRCS(
    info.cpp
)

END()

# SDK export: omitted unavailable recurse targets
