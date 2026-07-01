CXXFLAGS = -std=c++17 -Wall -pthread -I.. -I../thirdparty/zeromq/include
LDFLAGS  = -L../thirdparty/zeromq/lib -lzmq -pthread

OBJS = ZMQChannel.o Port.o Log.o

all: libframework.a port_sync_smoke

libframework.a: $(OBJS)
	ar rcs $@ $^

%.o: %.cc
	$(CXX) $(CXXFLAGS) -c $< -o $@

port_sync_smoke: tests/port_sync_smoke.o libframework.a
	$(CXX) $(CXXFLAGS) -o $@ $^

tests/port_sync_smoke.o: tests/port_sync_smoke.cc
	$(CXX) $(CXXFLAGS) -c $< -o $@

clean:
	rm -f *.o *.a tests/*.o port_sync_smoke
