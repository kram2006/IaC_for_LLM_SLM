import asyncio
import websockets
import json
import logging
import time

class XenOrchestraClient:
    def __init__(self, url, username, password):
        if url.startswith("http"):
            url = url.replace("http", "ws", 1)
        if not url.endswith("/api/"):
            url = url.rstrip("/") + "/api/"
            
        self.url = url
        self.username = username
        self.password = password
        self._objects_cache = None
        self._cache_timestamp = 0
        self._cache_ttl = 10 # 10 seconds TTL
        self._lock = asyncio.Lock()

    async def _call(self, method, params=None):
        """Internal helper to call JSON-RPC via WebSocket"""
        try:
            async with websockets.connect(self.url, open_timeout=10, close_timeout=5, max_size=25 * 1024 * 1024) as ws:
                try:
                    login_payload = {
                        "jsonrpc": "2.0",
                        "method": "session.signIn",
                        "params": {"email": self.username, "password": self.password},
                        "id": "login"
                    }
                    await ws.send(json.dumps(login_payload))
                    login_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    
                    if "error" in login_resp:
                        logging.error(f"XO Login failed: {login_resp['error']}")
                        return None

                    call_payload = {
                        "jsonrpc": "2.0",
                        "method": method,
                        "params": params or {},
                        "id": "call"
                    }
                    await ws.send(json.dumps(call_payload))
                    call_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                    
                    if "error" in call_resp:
                        logging.error(f"XO Method {method} failed: {call_resp['error']}")
                        return None
                        
                    return call_resp.get("result")
                except asyncio.TimeoutError:
                    logging.error(f"XO WebSocket timeout during {method}")
                    return None
        except Exception as e:
            logging.error(f"XO WebSocket communication error: {str(e)}")
            return None

    async def verify_vms(self, expected_count=None):
        """Asynchronous VM verification with TTL caching"""
        try:
            async with self._lock:
                now = time.time()
                if self._objects_cache is None or (now - self._cache_timestamp) > self._cache_ttl:
                    vms = await self._call("xo.getAllObjects")
                    if vms:
                        self._objects_cache, self._cache_timestamp = vms, now
                    else:
                        logging.warning("Failed to refresh XO objects cache.")
                
                vms = self._objects_cache
            
            if vms is None:
                return {"vms_exist_in_xo": False, "actual_vm_count": 0, "vm_details": [], "note": "No XO Connection"}

            vdis = {id: obj for id, obj in vms.items() if obj.get('type') == 'VDI'}
            vbds = {id: obj for id, obj in vms.items() if obj.get('type') == 'VBD'}
            my_vms = {id: obj for id, obj in vms.items() if obj.get('type') == 'VM' and not obj.get('is_control_domain')}
            
            vm_details = []
            for vid, vm in my_vms.items():
                vbd_ids = vm.get('$VBDs') or vm.get('VBDs') or []
                total_disk_bytes = 0
                for vbd_id in vbd_ids:
                    vbd = vbds.get(vbd_id)
                    if vbd and vbd.get('type') != 'CD':
                        vdi_id = vbd.get('VDI')
                        vdi = vdis.get(vdi_id) if vdi_id else None
                        if vdi and isinstance(vdi.get('size'), (int, float)):
                            total_disk_bytes += int(vdi['size'])

                addresses = vm.get('addresses') or {}
                ip_address = next(iter(addresses.values()), "unknown") if isinstance(addresses, dict) else "unknown"
                
                cpus_obj = vm.get('CPUs') or vm.get('$CPUs') or {}
                cpu_count = cpus_obj.get('number', 0) if isinstance(cpus_obj, dict) else (cpus_obj if isinstance(cpus_obj, int) else 0)
                
                memory_obj = vm.get('memory') or vm.get('$memory') or {}
                if isinstance(memory_obj, dict):
                    static_mem = memory_obj.get('static', [0, 0]) or [0, 0]
                    memory_max = int(static_mem[1]) if isinstance(static_mem, (list, tuple)) and len(static_mem) >= 2 else int(memory_obj.get('size', 0) or 0)
                else:
                    memory_max = 0
                
                vm_details.append({
                    "name": vm.get('name_label'),
                    "uuid": vid, 
                    "ip": ip_address,
                    "status": vm.get('power_state', 'unknown'),
                    "cpus": cpu_count,
                    "ram_gb": round(memory_max / (1024**3), 2),
                    "disk_gb": round(total_disk_bytes / (1024**3), 2)
                })

            return {
                "vms_exist_in_xo": len(my_vms) > 0,
                "expected_vm_count": expected_count or 1,
                "actual_vm_count": len(my_vms),
                "all_vms_running": all(vm.get('power_state') == 'Running' for vm in my_vms.values()),
                "vm_details": vm_details
            }
        except Exception as e:
            logging.error(f"XO Verification failed: {str(e)}")
            return {"error": str(e)}
