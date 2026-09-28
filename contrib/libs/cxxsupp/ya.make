LIBRARY()

WITHOUT_LICENSE_TEXTS()

VERSION(Service-proxy-version)

LICENSE(Service-Sourceless-Library)

NO_PLATFORM()

IF (NOT USE_STL_SYSTEM)
    IF (MSVC AND NOT CLANG_CL)
        PEERDIR(
            contrib/libs/cxxsupp/libcxxmsvc
        )
    ELSEIF (NVCC_STD_VER == "17" OR CUDA11)
        PEERDIR(
            contrib/libs/cxxsupp/libcxxcuda11
        )
    ELSE()
        PEERDIR(
            contrib/libs/cxxsupp/libcxx
        )
    ENDIF()
ELSE()
    PEERDIR(
        contrib/libs/cxxsupp/system_stl
    )
ENDIF()

END()

IF (NOT USE_STL_SYSTEM)
    IF (MSVC AND NOT CLANG_CL)
        # SDK export: omitted unavailable recurse targets
    ELSEIF (NVCC_STD_VER == "17" OR CUDA11)
        # SDK export: omitted unavailable recurse targets
    ELSE()
        RECURSE(
    libcxx
)
    ENDIF()

    RECURSE(
    libcxxabi
    libcxxrt
)
ENDIF()
