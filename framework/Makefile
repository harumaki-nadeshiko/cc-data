CXXFLAGS = -std=c++17 -Wall -I.. -pthread

OBJS = PseudoMemPort.o PseudoManager.o

all: libframework.a

libframework.a: $(OBJS)
	ar rcs $@ $^

%.o: %.cc
	$(CXX) $(CXXFLAGS) -c $< -o $@

clean:
	rm -f *.o *.a
