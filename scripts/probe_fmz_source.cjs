// Execute the supplied JavaScript only against a local fake exchange.
// No HTTP client, real credentials, or exchange SDK is made available to the VM.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const folder = path.join(root, 'reports/fmz_v2/source_review');
const source = fs.readFileSync(path.join(folder, 'original.js'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'source_manifest.json'), 'utf8'));
function context(positions=[]) {
  const calls = [], sleeps = [];
  let direction = '';
  const exchange = {
    GetAccount: () => ({Balance:100, Equity:100}), GetPosition: () => positions,
    GetOrders: () => [], GetTicker: () => ({Last:60000}),
    SetDirection: x => {direction=x;},
    Buy: (price, amount) => {calls.push({direction, price, amount}); return null;},
    Sell: (price, amount) => {calls.push({direction, price, amount}); return null;},
    CancelOrder: () => true, SetContractType:()=>{}, SetMarginLevel:()=>{}, IO:()=>{}
  };
  const c = {...manifest.defaults, exchange, Date:{now:()=>100000},
    _C:(fn,...args)=>fn(...args), Sleep:ms=>sleeps.push(ms), Log:()=>{}, LogProfit:()=>{},
    PD_LONG:0, PD_SHORT:1, ORDER_TYPE_BUY:0, ORDER_TYPE_SELL:1, ORDER_OFFSET_OPEN:0, ORDER_OFFSET_CLOSE:1};
  vm.createContext(c);
  vm.runInContext(source,c,{timeout:1000});
  c.originAmout=100;
  return {c,calls,sleeps};
}
const fresh=context();
vm.runInContext('timeBalance(true); setOrders(60000)',fresh.c,{timeout:1000});
const resumed=context([{Type:0,Amount:.002,Price:60000,Profit:0}]);
vm.runInContext('setOrders(60000); setOrders(60000)',resumed.c,{timeout:1000});
const cut=context([{Type:0,Amount:.005,Price:60000,Profit:-5},{Type:1,Amount:.001,Price:60000,Profit:1}]);
vm.runInContext('setOrders(60000)',cut.c,{timeout:1000});
const result = {fresh_btc_orders:fresh.calls, restart_orders:resumed.calls,
  index_after_two_rejected_calls:resumed.c.longIndex, cut_sleeps:cut.sleeps,
  assertions:{fresh_order_rounded_to_zero:fresh.calls.some(x=>x.amount===0),
    restart_has_nonpositive_limit:resumed.calls.some(x=>x.price<=0 && x.price!==-1),
    index_advances_on_rejected_order:resumed.c.longIndex===2,
    long_cut_blocks_for_72_hours:cut.sleeps.includes(72*3600000)}};
if(!Object.values(result.assertions).every(Boolean)) throw Error(JSON.stringify(result));
fs.writeFileSync(path.join(folder,'source_probe.json'),JSON.stringify(result,null,2));
process.stdout.write(JSON.stringify(result,null,2));
