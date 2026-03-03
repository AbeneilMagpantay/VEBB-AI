CXX = g++
CXXFLAGS = -std=c++20 -O3 -Wall -Wextra -I./include -march=native -flto
LDFLAGS = -lrt -lpthread

TARGET = execution_core
SRC = src/execution_core.cpp

all: $(TARGET)

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) -o $(TARGET) $(SRC) $(LDFLAGS)

clean:
	rm -f $(TARGET)
