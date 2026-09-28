#include <ydb/services/udf_store/wasm/abi/bridge.h>
#include <ydb/services/udf_store/wasm/abi/udf_cpp_abi.h>

using namespace NYdb::NUdfStore::NAbi;

extern "C" __attribute__((visibility("default")))
void hello(TExpressionContext*, uint64_t* result, uint64_t value) {
    *result = MakeInt64(BridgeGetInt64(value)).Release();
}
