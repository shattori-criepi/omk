/* Separate TU isolates fake hardware from the registration/sender adapters. */
#include <stdio.h>
#include "network_diagnostics.c"
#include "network_diagnostics_store.c"
bool diagnostic_runtime_page(unsigned p,char *out,size_t n) {(void)p;(void)out;(void)n;return false;}
void diag_bridge_fault(void) {
    network_diag_start();
    uint32_t t=network_diag_begin(ND_MESH_UP);
    fake_us+=31000000;network_diag_end(t,0,false);observe_once();
}
void diag_bridge_advance(uint64_t us) { fake_us=us;observe_once(); }
unsigned diag_bridge_saves(void) { return saves; }
