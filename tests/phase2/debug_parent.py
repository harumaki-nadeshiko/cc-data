import sys
import m5
from m5.objects import *
import os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import EPNodeWrapper

print("START")
ruby = RubySystem(num_of_sequencers=12, number_of_virtual_networks=4)
ruby.network = SimpleNetwork()
ruby.clk_domain = SrcClockDomain(clock="2GHz")
print("ruby OK")

ep = EPRNFController(ruby_system=ruby, node_id=0, data_channel_size=32)
print(f"ep created: node_id={ep.node_id}, has_parent={ep.has_parent()}")

w = EPNodeWrapper(ruby)
print(f"wrapper has_parent={w.has_parent()}")
w.setController(ep)
print(f"after setController: ep has_parent={ep.has_parent()}")
w.connectController(ep)
print(f"after connectController: ep has_parent={ep.has_parent()}")
print("DONE")
