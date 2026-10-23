#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_NO_MEM -1
#define ESP_ERR_INVALID_STATE -2
#define ESP_ERR_ESP_NETIF_DHCP_ALREADY_STARTED -3
#define pdTRUE 1
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#define CONFIG_MESH_ROUTE_TABLE_SIZE 50
#define WIFI_IF_STA 0
#define WIFI_IF_AP 1
#define MESH_PROTO_AP 1
#define MESH_PROTO_STA 2
#define IPADDR_TYPE_V4 0
#define ESP_NETIF_OP_SET 0
#define ESP_NETIF_DOMAIN_NAME_SERVER 0
#define ESP_NETIF_DNS_MAIN 0
#define OFFER_DNS 1
#define ESP_IP4TOADDR(a,b,c,d) ((uint32_t)(a) | ((b)<<8) | ((c)<<16) | ((d)<<24))
#define ESP_LOGI(tag,...) ((void)(tag))
#define ESP_LOGW(tag,...) ((void)(tag))
#define ESP_LOGE(tag,...) ((void)(tag))
typedef int dhcps_offer_t;
typedef int esp_netif_dhcp_status_t;
enum {ESP_NETIF_DHCP_INIT, ESP_NETIF_DHCP_STARTED, ESP_NETIF_DHCP_STOPPED};
typedef struct {uint32_t addr;} ip4_t;
typedef struct {ip4_t ip,gw,netmask;} esp_netif_ip_info_t;
typedef struct {struct {union {ip4_t ip4;} u_addr;int type;} ip;} esp_netif_dns_info_t;
typedef struct {const char *if_desc;const esp_netif_ip_info_t *ip_info;} esp_netif_inherent_config_t;
typedef struct {esp_netif_inherent_config_t *base;void *driver,*stack;} esp_netif_config_t;
static esp_netif_inherent_config_t wifi_base={"wifi_sta",NULL};
#define ESP_NETIF_DEFAULT_WIFI_STA() { .base=&wifi_base }
#define ESP_NETIF_INHERENT_DEFAULT_WIFI_STA() { .if_desc="wifi_sta" }
#define ESP_NETIF_INHERENT_DEFAULT_WIFI_AP() { .if_desc="wifi_ap" }
#define ESP_NETIF_NETSTACK_DEFAULT_WIFI_STA NULL
#define ESP_NETIF_NETSTACK_DEFAULT_WIFI_AP NULL

typedef struct netif esp_netif_t;
typedef struct {esp_netif_t *netif;int (*post_attach)(esp_netif_t *,void *);} esp_netif_driver_base_t;
typedef struct {
    void *handle;
    int (*transmit)(void *,void *,size_t);
    int (*transmit_wrap)(void *,void *,size_t,void *);
    void (*driver_free_rx_buffer)(void *,void *);
} esp_netif_driver_ifconfig_t;
struct netif {
    const char *desc;void *driver;bool alive,up,started,napt,wifi;
    int client,server;esp_netif_ip_info_t ip;
    esp_netif_driver_ifconfig_t callbacks;
};
typedef struct {esp_netif_t *esp_netif;esp_netif_ip_info_t ip_info;bool ip_changed;} ip_event_got_ip_t;
typedef struct {uint8_t addr[6];} mesh_addr_t;
typedef struct {uint8_t *data;size_t size;int proto,tos;} mesh_data_t;
typedef struct {unsigned rx_success_count,tx_success_count,tx_failure_count,last_rx_success_uptime_s,last_tx_success_uptime_s;} mesh_netif_diagnostics_t;
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
typedef int omk_mesh_tx_policy_t;
typedef pthread_mutex_t StaticSemaphore_t;
typedef pthread_mutex_t *SemaphoreHandle_t;
typedef int StaticQueue_t;
typedef void *QueueHandle_t;
static QueueHandle_t tx_queue;
static StaticQueue_t tx_queue_control;
static uint8_t tx_queue_storage[16];
#define MESH_TX_SLOTS 4
static unsigned tx_generation, callback_generation;
static void invalidate_tx(void) {++tx_generation;}
static void transmit_task(void *p) {(void)p;}
static unsigned uptime_seconds(void) {return 1;}
static int transmit_root_ap(void *a,void *b,size_t c) {(void)a;(void)b;(void)c;callback_generation=tx_generation;return 0;}
static int transmit_root_ap_wrap(void *a,void *b,size_t c,void *d) {(void)d;return transmit_root_ap(a,b,c);}
static int transmit_child_sta(void *a,void *b,size_t c) {return transmit_root_ap(a,b,c);}
static int transmit_child_sta_wrap(void *a,void *b,size_t c,void *d) {return transmit_root_ap_wrap(a,b,c,d);}

/* Test scheduler gates: no sleep/timing assumptions. */
static pthread_mutex_t gate=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed=PTHREAD_COND_INITIALIZER;
static bool hold_receive,rx_inside,release_receive,stop_attempt,stop_done,unsafe_destroy;
static bool root_role,deny_input;
static bool hold_destroy,destroy_inside,release_destroy,rx_waiter;
static unsigned received,drained,pbufs,driver_count,netif_count,created,link_starts;
static unsigned active_refs;
static const char *failure;
static int fail_countdown=1;
static bool fail(const char *name) {
    if(failure && strcmp(name,failure)==0 && --fail_countdown==0) {failure=NULL;return true;}
    return false;
}
static void inject(const char *name) {failure=name;fail_countdown=1;}
static void *drivers[4096];
static unsigned driver_used;
static bool driver_alive(void *p) {
    for(unsigned i=0;i<driver_used;i++) if(drivers[i]==p) return true;
    return false;
}
static void *allocate_driver(size_t size) {
    if(fail("calloc")) return NULL;
    void *p=calloc(1,size);assert(p && driver_used<4096);
    drivers[driver_used++]=p;++driver_count;return p;
}
static void release_driver(void *p) {
    if(!p) return;
    for(unsigned i=0;i<driver_used;i++) if(drivers[i]==p) {
        drivers[i]=NULL;--driver_count;free(p);return;
    }
    assert(!"double free / unowned buffer");
}
static void *host_calloc(size_t n,size_t size) {return allocate_driver(n*size);}
static esp_netif_t arena[4096];
static esp_netif_t *wifi_netif;
static bool wifi_handlers;
static esp_netif_t *pending[64];
static uint8_t pending_first[64];
static unsigned pending_count;
static void drain_input(void) {
    for(unsigned i=0;i<pending_count;i++) {
        esp_netif_t *n=pending[i];
        assert(n->alive && driver_alive(n->driver));
        /* A pending input can invoke driver TX before teardown completes. */
        if(n->callbacks.transmit) n->callbacks.transmit(n->driver,&pending_first[i],1);
        ++drained;--pbufs;
    }
    pending_count=0;
}
static void live(esp_netif_t *n) {assert(n && n->alive);}
static esp_netif_t *esp_netif_new(const esp_netif_config_t *c) {
    if(fail("new")) return NULL;
    assert(created<4096);esp_netif_t *n=&arena[created++];
    *n=(esp_netif_t){.desc=c->base->if_desc,.alive=true};
    if(c->base->ip_info) n->ip=*c->base->ip_info;
    ++netif_count;return n;
}
static int esp_netif_set_driver_config(esp_netif_t *n,const esp_netif_driver_ifconfig_t *c) {
    live(n);if(fail("driver_config")) return -1;n->callbacks=*c;n->driver=c->handle;return 0;
}
static int esp_netif_attach(esp_netif_t *n,void *d) {
    live(n);n->driver=d;
    if(fail("attach")) return -1;
    return ((esp_netif_driver_base_t *)d)->post_attach(n,d);
}
static void *esp_netif_get_io_driver(esp_netif_t *n) {live(n);return n->driver;}
static const char *esp_netif_get_desc(esp_netif_t *n) {live(n);return n->desc;}
static int esp_netif_attach_wifi_station(esp_netif_t *n) {
    wifi_netif=n;n->wifi=true;
    if(fail("wifi_attach")) return -1;
    n->driver=allocate_driver(sizeof(esp_netif_driver_base_t));
    if(!n->driver) return -1;
    return fail("driver_config") ? -1 : 0;
}
static int esp_wifi_set_default_wifi_sta_handlers(void) {
    if(fail("handlers")) return -1;
    wifi_handlers=true;return 0;
}
static int esp_wifi_clear_default_wifi_driver_and_handlers(esp_netif_t *n) {
    live(n);assert(wifi_netif==n);wifi_netif=NULL;wifi_handlers=false;
    release_driver(n->driver);n->driver=NULL;return 0;
}
static int esp_wifi_get_mac(int iface,uint8_t *mac) {
    if(fail(iface==WIFI_IF_AP ? "ap_mac" : "sta_mac")) return -1;
    memset(mac,iface+1,6);return 0;
}
static int esp_netif_set_mac(esp_netif_t *n,uint8_t *mac) {live(n);assert(mac);return fail("set_mac") ? -1 : 0;}
static bool esp_mesh_is_root(void) {return root_role;}
static int esp_netif_receive(void *handle,void *buffer,size_t size,void *l2) {
    esp_netif_t *n=handle;assert(l2==NULL && buffer && size);
    pthread_mutex_lock(&gate);live(n);++active_refs;++received;
    if(hold_receive) {
        rx_inside=true;pthread_cond_broadcast(&changed);
        while(!release_receive) pthread_cond_wait(&changed,&gate);
    }
    if(!n->alive) unsafe_destroy=true;
    else if(n->up && !deny_input) {
        assert(pending_count<64);pending[pending_count]=n;
        pending_first[pending_count++]=*(uint8_t *)buffer;++pbufs;
    }
    --active_refs;pthread_mutex_unlock(&gate);return 0;
}
static int esp_wifi_register_if_rxcb(void *d,int (*fn)(void *,void *,size_t,void *),void *n) {
    assert(driver_alive(d) && fn==esp_netif_receive && n);++link_starts;
    return fail("rxcb") ? -1 : 0;
}
static void esp_netif_action_start(esp_netif_t *n,void *b,int i,void *d) {
    (void)b;(void)i;(void)d;live(n);drain_input();
    if(fail("start_action")) return;
    n->started=true;
    if(strstr(n->desc,"ap")) {n->up=true;n->server=ESP_NETIF_DHCP_STARTED;}
}
static void esp_netif_action_connected(esp_netif_t *n,void *b,int i,void *d) {
    (void)b;(void)i;(void)d;live(n);drain_input();
    if(fail("connect_action") || !n->started) return;
    n->up=true;n->client=ESP_NETIF_DHCP_STARTED;
}
static void esp_netif_action_disconnected(esp_netif_t *n,void *b,int i,void *d) {
    (void)b;(void)i;(void)d;live(n);drain_input();n->up=false;n->client=ESP_NETIF_DHCP_INIT;
}
static void esp_netif_action_stop(esp_netif_t *n,void *b,int i,void *d) {
    esp_netif_action_disconnected(n,b,i,d);n->started=false;n->server=ESP_NETIF_DHCP_INIT;
}
static void esp_netif_destroy(esp_netif_t *n) {
    pthread_mutex_lock(&gate);live(n);
    if(active_refs) unsafe_destroy=true;
    if(hold_destroy) {
        destroy_inside=true;pthread_cond_broadcast(&changed);
        while(!release_destroy) pthread_cond_wait(&changed,&gate);
    }
    drain_input();assert(!n->napt);n->alive=false;--netif_count;
    pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);
}
static int esp_netif_dhcpc_get_status(esp_netif_t *n,int *s) {
    live(n);if(fail("client_status")) return -1;*s=n->client;return 0;
}
static int esp_netif_dhcps_get_status(esp_netif_t *n,int *s) {
    live(n);if(fail("server_status")) return -1;*s=n->server;return 0;
}
static int esp_netif_dhcps_start(esp_netif_t *n) {live(n);return fail("server_start") ? -1 : 0;}
static int esp_netif_dhcps_stop(esp_netif_t *n) {live(n);n->server=ESP_NETIF_DHCP_STOPPED;return 0;}
static int esp_netif_dhcps_option(esp_netif_t *n,int a,int b,void *v,size_t z) {
    live(n);(void)a;(void)b;(void)v;(void)z;return fail("dns_offer") ? -1 : 0;
}
static int esp_netif_set_dns_info(esp_netif_t *n,int a,const esp_netif_dns_info_t *d) {
    live(n);(void)a;(void)d;return fail("dns") ? -1 : 0;
}
static int esp_netif_get_ip_info(esp_netif_t *n,esp_netif_ip_info_t *ip) {live(n);*ip=n->ip;return 0;}
static int esp_netif_napt_enable(esp_netif_t *n) {live(n);if(fail("napt")) return -1;assert(n->up);n->napt=true;return 0;}
static int esp_netif_napt_disable(esp_netif_t *n) {live(n);n->napt=false;return 0;}
static void ip_napt_enable(uint32_t ip,int enable) {(void)ip;(void)enable;}
static const char *esp_err_to_name(int e) {(void)e;return "error";}
static SemaphoreHandle_t xSemaphoreCreateMutexStatic(StaticSemaphore_t *m) {assert(!pthread_mutex_init(m,NULL));return m;}
static int xSemaphoreTake(SemaphoreHandle_t m,unsigned timeout) {
    assert(timeout==portMAX_DELAY);
    pthread_mutex_lock(&gate);
    if(rx_inside && !release_receive) {stop_attempt=true;pthread_cond_broadcast(&changed);}
    if(destroy_inside && !release_destroy) {rx_waiter=true;pthread_cond_broadcast(&changed);}
    pthread_mutex_unlock(&gate);
    assert(!pthread_mutex_lock(m));return 1;
}
static int xSemaphoreGive(SemaphoreHandle_t m) {assert(!pthread_mutex_unlock(m));return 1;}
static QueueHandle_t xQueueCreateStatic(int n,size_t s,void *b,void *c) {
    (void)n;(void)s;(void)b;(void)c;return fail("queue") ? NULL : (void *)1;
}
static void vQueueDelete(void *q) {assert(q);}
static int xTaskCreate(void (*fn)(void *),const char *name,int stack,void *arg,int prio,void *handle) {
    (void)fn;(void)name;(void)stack;(void)arg;(void)prio;(void)handle;return fail("task") ? 0 : pdPASS;
}
static void vTaskDelete(void *p) {(void)p;}
static int esp_mesh_recv(mesh_addr_t *,mesh_data_t *,unsigned,int *,void *,int);
#define calloc host_calloc
#define free release_driver
/* PRODUCTION */
#undef calloc
#undef free

static int esp_mesh_recv(mesh_addr_t *from,mesh_data_t *data,unsigned ticks,int *flags,void *opts,int count) {
    (void)from;(void)ticks;(void)flags;(void)opts;(void)count;
    data->data[0]=42;data->size=64;data->proto=root_role ? MESH_PROTO_AP : MESH_PROTO_STA;
    receive_task_running=false;return ESP_OK;
}
static void boot(void) {
    assert(mesh_netifs_init()==0);
    /* SDK WIFI_EVENT_STA_START, before Mesh parent-connected. */
    esp_netif_action_start(station_netif,NULL,0,NULL);
}
static void child(void) {root_role=false;assert(mesh_netifs_start(false)==0);}
static void root(void) {root_role=true;assert(mesh_netifs_start(true)==0);}
static void cleanup(void) {
#ifdef FIXED_LIFECYCLE
    lifecycle_enter();destroy_mesh_ap();destroy_station();lifecycle_leave();
#endif
    assert(netif_count==0 && driver_count==0 && !wifi_netif && !wifi_handlers && !pbufs);
}
static void *rx_thread(void *p) {(void)p;receive_task_running=true;receive_task(NULL);return NULL;}
static void *stop_thread(void *p) {
    (void)p;assert(mesh_netifs_stop()==0);
    pthread_mutex_lock(&gate);stop_done=true;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);return NULL;
}
static void race(void) {
    boot();child();hold_receive=true;
    pthread_t rx,stop;assert(!pthread_create(&rx,NULL,rx_thread,NULL));
    pthread_mutex_lock(&gate);while(!rx_inside) pthread_cond_wait(&changed,&gate);pthread_mutex_unlock(&gate);
    assert(!pthread_create(&stop,NULL,stop_thread,NULL));
    pthread_mutex_lock(&gate);
    while(!stop_attempt && !stop_done) pthread_cond_wait(&changed,&gate);
    release_receive=true;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);
    pthread_join(rx,NULL);pthread_join(stop,NULL);
    assert(!unsafe_destroy);assert(drained==1 && !pbufs);assert(callback_generation<tx_generation);cleanup();
}
#ifdef FIXED_LIFECYCLE
static uint32_t waiting_generation;
static void *late_rx_thread(void *unused) {
    (void)unused;uint8_t bytes[64]={42};
    mesh_data_t data={.data=bytes,.size=64,.proto=MESH_PROTO_STA};
    receive_frame(&data,waiting_generation);return NULL;
}
#endif
static void late_rx(void) {
#ifdef FIXED_LIFECYCLE
    boot();child();waiting_generation=rx_generation;hold_destroy=true;
    pthread_t rx,stop;assert(!pthread_create(&stop,NULL,stop_thread,NULL));
    pthread_mutex_lock(&gate);while(!destroy_inside) pthread_cond_wait(&changed,&gate);pthread_mutex_unlock(&gate);
    assert(!pthread_create(&rx,NULL,late_rx_thread,NULL));
    pthread_mutex_lock(&gate);while(!rx_waiter) pthread_cond_wait(&changed,&gate);
    release_destroy=true;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);
    pthread_join(stop,NULL);pthread_join(rx,NULL);
    assert(!received && !unsafe_destroy);cleanup();
#endif
}
static void partial(void) {
    boot();root();inject("attach");
    assert(mesh_netif_start_root_ap(true,1)!=0);
    assert(mesh_ap_netif==NULL); /* baseline incorrectly retains partial AP */
    assert(mesh_netif_start_root_ap(true,1)==0);assert(mesh_ap_netif);
    assert(driver_count==2 && netif_count==2);cleanup();
}
static void failures(void) {
#ifdef FIXED_LIFECYCLE
    const char *init_fail[]={"queue","task","new","wifi_attach","calloc","driver_config","handlers"};
    for(unsigned i=0;i<sizeof(init_fail)/sizeof(*init_fail);i++) {
        if(i==1) tx_queue=NULL;
        inject(init_fail[i]);assert(mesh_netifs_init()!=0);assert(!failure);
        assert(!station_netif && !wifi_netif && !wifi_handlers && !netif_count && !driver_count);
        boot();cleanup();
    }
    const char *child_fail[]={"new","calloc","attach","driver_config","sta_mac","set_mac","start_action","connect_action","client_status"};
    const char *ap_fail[]={"new","calloc","attach","driver_config","sta_mac","ap_mac","set_mac","dns_offer","dns","server_start","start_action","server_status","napt"};
    const char *wifi_fail[]={"new","wifi_attach","calloc","driver_config","handlers","sta_mac","set_mac","rxcb"};
    for(unsigned cycle=0;cycle<8;cycle++) {
        for(unsigned i=0;i<sizeof(child_fail)/sizeof(*child_fail);i++) {
            boot();inject(child_fail[i]);assert(mesh_netifs_start(false)!=0);assert(!failure);
            assert(!station_netif && !netif_count && !driver_count && !wifi_netif);
            child();cleanup();
        }
        for(unsigned i=0;i<sizeof(ap_fail)/sizeof(*ap_fail);i++) {
            boot();root();inject(ap_fail[i]);assert(mesh_netif_start_root_ap(true,1)!=0);assert(!failure);
            assert(!mesh_ap_netif && netif_count==1 && driver_count==1);
            assert(mesh_netif_start_root_ap(true,1)==0);cleanup();
        }
        for(unsigned i=0;i<sizeof(wifi_fail)/sizeof(*wifi_fail);i++) {
            boot();child();inject(wifi_fail[i]);assert(mesh_netifs_stop()!=0);assert(!failure);
            assert(!station_netif && !netif_count && !driver_count && !wifi_netif);
            assert(mesh_netifs_stop()==0);cleanup();
        }
    }
#endif
}
static void roles(void) {
#ifdef FIXED_LIFECYCLE
    boot();unsigned count=created;assert(mesh_netifs_init()==0 && created==count);
    root();count=created;unsigned starts=link_starts;root();assert(created==count && link_starts==starts);
    assert(station_netif->client==ESP_NETIF_DHCP_STARTED);
    assert(mesh_netif_start_root_ap(true,1)==0);count=created;
    assert(mesh_netif_start_root_ap(true,1)==0 && created==count);
    child();assert(!mesh_ap_netif && station_is_child && !wifi_netif);
    count=created;child();assert(created==count);
    root();assert(!station_is_child && wifi_netif==station_netif);
    assert(mesh_netifs_stop()==0);count=created;
    assert(mesh_netifs_stop()==0 && created==count && !station_connected);
    root();assert(station_netif->client==ESP_NETIF_DHCP_STARTED);cleanup();
#endif
}
static void buffers(void) {
#ifdef FIXED_LIFECYCLE
    boot();child();uint8_t bytes[64]={42};mesh_data_t data={.data=bytes,.size=64,.proto=MESH_PROTO_STA};
    uint32_t old=rx_generation;
    receive_frame(&data,old);assert(pbufs==1);bytes[0]=99;assert(pending_first[0]==42);
    deny_input=true;receive_frame(&data,old);assert(pbufs==1);deny_input=false;
    assert(mesh_netifs_stop()==0);assert(drained==1 && !pbufs);
    unsigned count=received;receive_frame(&data,old);assert(received==count);
    child();receive_frame(&data,old);assert(received==count);
    root_role=true;receive_frame(&data,rx_generation);assert(received==count); // role changed, event still pending
    root();assert(mesh_netif_start_root_ap(true,1)==0);
    data.proto=MESH_PROTO_AP;receive_frame(&data,rx_generation);assert(pbufs==1);
    cleanup();assert(!pbufs);
#endif
}
static void events(void) {
#ifdef FIXED_LIFECYCLE
    boot();child();station_netif->ip.ip.addr=123;
    ip_event_got_ip_t event={.esp_netif=station_netif,.ip_info=station_netif->ip};
    assert(mesh_netif_accept_ip_event(&event, true));
    assert(!mesh_netif_accept_ip_event(&event, false)); // queued loss after reacquisition
    station_netif->ip.ip.addr=0;
    assert(mesh_netif_accept_ip_event(&event, false));
    station_netif->ip.ip.addr=123;
    root_role=true;assert(!mesh_netif_accept_ip_event(&event, true));root();
    assert(!mesh_netif_accept_ip_event(&event, true));
    event.esp_netif=station_netif; // allocator address reuse / old IP payload
    assert(!mesh_netif_accept_ip_event(&event, true));
    station_netif->ip.ip.addr=456;event.ip_info=station_netif->ip;
    assert(mesh_netif_accept_ip_event(&event, true));
    assert(mesh_netifs_stop()==0);assert(!mesh_netif_accept_ip_event(&event, true));cleanup();
#endif
}
int main(int argc,char **argv) {
    (void)routing_table;(void)tx_policy_lock;(void)tx_policy;
    assert(argc==2);
    if(!strcmp(argv[1],"rx_destroy")) race();
    else if(!strcmp(argv[1],"partial_retry")) partial();
    else if(!strcmp(argv[1],"late_rx")) late_rx();
    else if(!strcmp(argv[1],"failures")) failures();
    else if(!strcmp(argv[1],"roles")) roles();
    else if(!strcmp(argv[1],"buffers")) buffers();
    else if(!strcmp(argv[1],"events")) events();
    else assert(0);
    return 0;
}
