CXXFLAGS = -std=c++17 -Wall -pthread -I.. -I../thirdparty/zeromq/include
LDFLAGS  = -L../thirdparty/zeromq/lib -lzmq -pthread

OBJS = Port.o Log.o

all: libframework_local.a iface_contract_test

libframework_local.a: $(OBJS)
	ar rcs $@ $^

%.o: %.cc
	$(CXX) $(CXXFLAGS) -c $< -o $@

iface_contract_test: tests/iface_contract_test.o libframework_local.a
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

tests/iface_contract_test.o: tests/iface_contract_test.cc
	$(CXX) $(CXXFLAGS) -c $< -o $@

test: iface_contract_test
	./iface_contract_test

clean:
	rm -f *.o *.a tests/*.o iface_contract_test
