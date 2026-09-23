/*backtest
start: 2022-09-01 00:00:00
end: 2023-08-01 00:00:00
period: 1m
basePeriod: 1m
exchanges: [{"eid":"Futures_Binance","currency":"ETH_USDT"},"fee":[0.04,0.04]}]
*/
var baseSpacing  = 0;

var originBaseAmount = 0
var originAmout = 0
var lastBalance = 0 ;

var lastSell = Date.now()
var _profitTarget = 0;
var lastCheck = Date.now();
var lastLog = Date.now()
var checkInterval = 1000 ; 
var logInterval = 3600000 ; 

var cutlosstime = 0 ;
var cutlossprofite = 0 ;

nowBal = 0 ; 

baseAmount = Number(exchange.GetAccount().Balance)*baseAmountRate;

function timeBalance(init)
{
    try{
        if((Date.now() - lastCheck > checkInterval) || init)
        {
            lastCheck = Date.now();
            var pos = _C(exchange.GetPosition)
            var p = getLong(pos, "pos")
            if (p) {
                p = p.Profit
            }else{
                p=0
            }

            var sp = getShort(pos,"pos");
            if(sp)
            {
                sp = sp.Profit
            }else{
                sp = 0;
            }
            p+=sp;

            
            baseAmount = (Number(exchange.GetAccount().Balance)+p)*baseAmountRate;
            if(baseAmount<baseAmountMini)
            {
                baseAmount = baseAmountMini
            }
            realBal = Number(exchange.GetAccount().Balance)+p-originAmout;
            nowBal = Number(exchange.GetAccount().Balance)
            if(Date.now() - lastLog > logInterval)
            {
                lastLog = Date.now()
                LogProfit(realBal)
                Log("🔥 Loss cut times :: "+cutlosstime + " :: P :: "+cutlossprofite)
            }
        
        }
    }catch(e)
    {
        Log(e)
    }
}

function cancelAll() {
    while (true) {
        var orders = _C(exchange.GetOrders)
        if (orders.length == 0) { 
            break 
        }
        for (var i = 0 ; i < orders.length ; i++) {
            exchange.CancelOrder(orders[i].Id, orders[i])
            Sleep(interval)
        }
    }
}


function cancelAllOpenLong() {
    while (true) {
        var orders = _C(exchange.GetOrders)
        // Log("🔥 cancelAllLong :: ",orders)
        var lock = true ;
        for (var i = 0 ; i < orders.length ; i++) {
            if(orders[i].Type==ORDER_TYPE_BUY && orders[i].Offset == ORDER_OFFSET_OPEN)
            {
                lock = false
                exchange.CancelOrder(orders[i].Id, orders[i])
                Sleep(interval)
            }
        }
        if (lock) { 
            return true;
            break 
        }
    }
}
function cancelAllCloseLong() {
    while (true) {
        var orders = _C(exchange.GetOrders)
        // Log("🔥 cancelAllLong :: ",orders)
        var lock = true ;
        for (var i = 0 ; i < orders.length ; i++) {
            if(orders[i].Type==ORDER_TYPE_SELL && orders[i].Offset == ORDER_OFFSET_CLOSE)
            {
                lock = false
                exchange.CancelOrder(orders[i].Id, orders[i])
                Sleep(interval)
            }
        }
        if (lock) { 
            return true;
            break 
        }
    }
    return true;
}
function cancelAllOpenShort() {
    while (true) {
        var orders = _C(exchange.GetOrders)
        var lock = true;
        for (var i = 0 ; i < orders.length ; i++) {
            if(orders[i].Type==ORDER_TYPE_SELL && orders[i].Offset == ORDER_OFFSET_OPEN)
            {
                exchange.CancelOrder(orders[i].Id, orders[i])
                Sleep(interval)
                lock = false;
            }
        }
        if (lock) { 
            return true;
            break 
        }
    }
}
function cancelAllCloseShort() {
    while (true) {
        var orders = _C(exchange.GetOrders)
        var lock = true;
        for (var i = 0 ; i < orders.length ; i++) {
            if(orders[i].Type==ORDER_TYPE_BUY && orders[i].Offset == ORDER_OFFSET_CLOSE)
            {
                exchange.CancelOrder(orders[i].Id, orders[i])
                Sleep(interval)
                lock = false;
            }
        }
        if (lock) { 
            return true;
            break 
        }
    }
}
function getLong(arr, kind) {
    var ret = null 
    for (var i = 0 ; i < arr.length ; i++) {
        if (arr[i].Type == (kind == "pos" ? PD_LONG : ORDER_TYPE_BUY)) {
            ret = arr[i]
        }
    }
    return ret
}
function getLongAnalyzePos(arr) {
    var ret = false
    for (var i = 0 ; i < arr.length ; i++) {
        if (arr[i].Type == PD_LONG) {
            ret=(arr[i])
        }
    }
    return ret
}
function getLongAnalyzeOrder(arr,kind) {
    var ret = []
    var a = 0 ;
    var b = 0;
    if(kind)
    {
        //Open long order
        a = ORDER_TYPE_BUY
        b = ORDER_OFFSET_OPEN
    }else{
        //Close long order
        a = ORDER_TYPE_SELL
        b = ORDER_OFFSET_CLOSE
    }
    for (var i = 0 ; i < arr.length ; i++) {
        if (arr[i].Type == a && arr[i].Offset == b) {
            ret .push(arr[i])
        }
    }
    return ret
}
function getShort(arr, kind) {
    var ret = null 
    for (var i = 0 ; i < arr.length ; i++) {
        if (arr[i].Type == (kind == "pos" ? PD_SHORT : ORDER_TYPE_SELL)) {
            ret = arr[i]
        }
    }
    return ret
}
function getShortAnalyzePos(arr) {
    var ret = false
    for (var i = 0 ; i < arr.length ; i++) {
        if (arr[i].Type == PD_SHORT) {
            ret =(arr[i])
        }
    }
    return ret
}
function getShortAnalyzeOrder(arr,kind) {
    var ret = []
    var a = 0 ;
    var b = 0;
    if(kind)
    {
        //Open long order
        a = ORDER_TYPE_SELL
        b = ORDER_OFFSET_OPEN
    }else{
        //Close long order
        a = ORDER_TYPE_BUY
        b = ORDER_OFFSET_CLOSE
    }
    for (var i = 0 ; i < arr.length ; i++) {
        if (arr[i].Type == a && arr[i].Offset == b) {
            ret .push(arr[i])
        }
    }
    return ret
}
function baseAmountCrypto(a)
{
    try{
        return a/(exchange.GetTicker()).Last 
    }catch(e)
    {
        Sleep(interval);
        return baseAmountCrypto(a)
    }
    
}

var longn = baseAmount;
var longIndex = 0 ;
var longFirstPrice = 0;
var longBaseSpacing = 0 ;

var shortn = baseAmount;
var shortIndex = 0 ;
var shortFirstPrice = 0;
var shortBaseSpacing = 0 ;

function setOrders(firstPrice)
{
    timeBalance()
    var pos = _C(exchange.GetPosition)
    var od = _C(exchange.GetOrders)
    var longPos = getLong(pos);
    var longBuyOrder = getLongAnalyzeOrder(od,true);
    var shortPos = getShort(pos);
    var shortBuyOrder = getShortAnalyzeOrder(od,true);

    if(!longPos&&shortIndex<=warningIndex)
    {
        cancelAllCloseLong();
        cancelAllOpenLong();
        longn = baseAmountCrypto(baseAmount)*ratio;
        longFirstPrice = firstPrice;
        longIndex = 0;
        exchange.SetDirection("buy")
        exchange.Buy(-1, Number(longn.toFixed(amountDecimal)))
        baseSpacing = baseSpacings*firstPrice
    }

    if(longPos&&longPos.Amount>=baseAmountCrypto(originAmout)*maxLoss)
    {
        Sleep(cutlosssleep)
        cancelAllCloseLong();
        cancelAllOpenLong();
        exchange.SetDirection("closebuy")
        exchange.Sell(-1, longPos.Amount)
        cutlosstime++
         Log("😭 Cut loss " + longPos.Profit)
         cutlossprofite+=longPos.Profit

        cancelAllCloseLong();
        cancelAllOpenLong();
        cancelAllCloseShort();
        cancelAllOpenShort();
         Sleep(3600000 * 72)
    }

    if(longPos && longBuyOrder.length == 0 )
    {
        longIndex++
        cancelAllCloseLong();
        cancelAllOpenLong();
        //New price and buy check . 
        longn = longn * ratio
        var price = Number((longFirstPrice -longIndex*baseSpacing).toFixed(priceDecimal));
        exchange.SetDirection("buy")
        exchange.Buy(price, Number(longn.toFixed(amountDecimal)))

        pos = _C(exchange.GetPosition)
        longPos = getLong(pos);
        if (longPos) {
                exchange.SetDirection("closebuy")
                exchange.Sell(
                    Number(Number(longPos.Price + firstPrice*profitTarget).toFixed(priceDecimal))
                    , longPos.Amount
                    )
        }
    }

    if(!shortPos && longIndex<=warningIndex)
    {
        cancelAllCloseShort();
        cancelAllOpenShort();
        shortn = baseAmountCrypto(baseAmount)*ratio;
        shortFirstPrice = firstPrice;
        shortIndex = 0;
        exchange.SetDirection("sell")
        exchange.Sell(-1, Number(shortn.toFixed(amountDecimal)))
        baseSpacing = baseSpacings*firstPrice
    }
    if(shortPos && shortPos.Amount>=baseAmountCrypto(originAmout)*maxLoss )
    {
        Sleep(cutlosssleep)
        cancelAllCloseShort();
        cancelAllOpenShort();
        exchange.SetDirection("closesell")
        exchange.Buy(-1, shortPos.Amount)
        cutlosstime++
        Log("😭 Cut loss " + shortPos.Profit)
        cutlossprofite+=shortPos.Profit
    }

    if(shortPos && shortBuyOrder.length == 0 )
    {
        shortIndex++
        cancelAllCloseShort();
        cancelAllOpenShort();
        shortn = shortn * ratio
        firstPrice = _C(exchange.GetTicker).Last;
        var price = Number((shortFirstPrice + shortIndex*baseSpacing).toFixed(priceDecimal));
        exchange.SetDirection("sell")
        exchange.Sell(price, Number(shortn.toFixed(amountDecimal)))

        pos = _C(exchange.GetPosition)
        shortPos = getShort(pos);
        if (shortPos) {
                exchange.SetDirection("closesell")
                exchange.Buy(
                    Number(Number(shortPos.Price - firstPrice*profitTarget).toFixed(priceDecimal))
                    , shortPos.Amount
                    )
        }
    }
    Sleep(interval)
}

function debug()
{
    // timeBalance()
    cancelAll()
    // pos = _C(exchange.GetPosition)
    // Log(pos)
    // Log(exchange.get)
    exchange.SetDirection("buy")
    exchange.Buy(2.64, 240.3) 

    exchange.Buy(2.625, 300) 

    exchange.Buy(2.61, 400) 

    exchange.SetDirection("closebuy")
    exchange.Sell(2.71, 944.1) 

    // exchange.SetDirection("closesell")
    // exchange.Buy(0.648, 7151.6) 
    // exchange.Sell(1784, 1.494) 

    // exchange.SetDirection("closesell")
    // exchange.Buy(0.613,4990.9 ); 

    // exchange.SetDirection("closesell")
    // exchange.Buy(0.668,10000 ); 

    // exchange.SetDirection("sell")
    // exchange.Sell(0.63, 4990) 

    // var longPos = getLong(pos, "pos")
    // exchange.Sell(-1, 2.396)
}
function main() {
    exchange.SetContractType("swap")
    exchange.SetMarginLevel(MarginLevel)
    exchange.IO ("api", "POST", "/fapi/v1/positionSide/dual", "dualSidePosition=true")
    // debug()

    Sleep(interval)
    originAmout = Number(exchange.GetAccount().Balance)
    while (true) {
        var p = _C(exchange.GetTicker).Last;
        timeBalance(true)
        setOrders(
            p
        )
    }
    
}
