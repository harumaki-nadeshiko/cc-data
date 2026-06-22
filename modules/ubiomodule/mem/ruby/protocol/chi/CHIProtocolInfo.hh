#ifndef __CHI_PROTOCOL_INFO_STUB__
#define __CHI_PROTOCOL_INFO_STUB__
namespace gem5 { namespace ruby { namespace CHI {
    enum CHIRequestType { ReadOnce=0, WriteUniqueFull=1 };
    enum CHIDataType { CompData_I=0, CompData_UC=1, CompData_SC=2 };
    enum CHIResponseType { Comp_I=0, Comp_UC=1, Comp_SC=2, Comp=3, CompDBIDResp=4, DBIDResp=5, RetryAck=6, PCrdGrant=7 };
    struct MachineID { int type; int num; };
    struct MessageSizeType_Control {};
} } }
#endif
