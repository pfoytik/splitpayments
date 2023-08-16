from http import HTTPStatus
from typing import List

import bech32
import websockets
import json

from fastapi import Depends
from loguru import logger
from starlette.exceptions import HTTPException

from lnbits.core.crud import get_wallet, get_wallet_for_key
from lnbits.decorators import WalletTypeInfo, check_admin, require_admin_key

from . import scheduled_tasks, splitpayments_ext
from .crud import get_targets, set_targets
from .models import Target, TargetPutList

@splitpayments_ext.get("/api/v1/targets")
async def api_targets_get(
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> List[Target]:
    targets = await get_targets(wallet.wallet.id)    
    return targets or []

@splitpayments_ext.put("/api/v1/targets", status_code=HTTPStatus.OK)
async def api_targets_set(
    target_put: TargetPutList,
    source_wallet: WalletTypeInfo = Depends(require_admin_key),
) -> None:
            
    try:
        targets: List[Target] = []
        for entry in target_put.targets:                        
            if entry.wallet.find("@") < 0 and entry.wallet.find("LNURL") < 0 and entry.wallet.find("npub") < 0:
                wallet = await get_wallet(entry.wallet)
                if not wallet:
                    wallet = await get_wallet_for_key(entry.wallet, "invoice")
                    if not wallet:                        
                        raise HTTPException(
                            status_code=HTTPStatus.BAD_REQUEST,
                            detail=f"Invalid wallet '{entry.wallet}'.",
                        )

                if wallet.id == source_wallet.wallet.id:
                    raise HTTPException(
                        status_code=HTTPStatus.BAD_REQUEST, detail="Can't split to itself."
                    )                        

            if entry.percent <= 0:
                raise HTTPException(
                    status_code=HTTPStatus.BAD_REQUEST,
                    detail=f"Invalid percent '{entry.percent}'.",
                )

            
            if entry.wallet.find("npub") >= 0:                   
                hrp, data = bech32.bech32_decode(entry.wallet)
                raw_secret = bech32.convertbits(data, 5, 8)
                if raw_secret[-1] != 0x0:
                    pubkey = str(bytes(raw_secret).hex())        
                else:
                    pubkey = str(bytes(raw_secret[:-1]).hex())        

                ## URI for relay used
                ## TODO - make this a variable assigned by the user with a default relat
                uri = "wss://nostr-pub.wellorder.net"
                jsonOb = ''
                
                async with websockets.connect(uri) as websocket:
                #websocket = websockets.connect(uri)
                    req = '["REQ", "a",  {"kinds": [0], "limit": 10, "authors": ["'+ pubkey +'"]} ]'
                    ''' send req to websocket and print response'''
                    await websocket.send(req)                    
                    greeting = await websocket.recv()
                    output = json.loads(greeting)
                    jsonOb = json.loads(output[2]['content'])
                                                    
                if "lud16" in jsonOb:
                    logger.info("we got a lud16: ", jsonOb["lud16"])
                    if len(jsonOb["lud16"]) > 1:
                        npubWallet = jsonOb["lud16"]
                elif "lud06" in jsonOb:
                    logger.info("we got a lud06: ", jsonOb["lud06"])
                    if len(jsonOb["lud06"]) > 1:
                        npubWallet = jsonOb["lud06"]                                                    
                else:
                    raise HTTPException(
                        status_code=HTTPStatus.BAD_REQUEST,
                        detail=f"Invalid wallet '{entry.wallet}'.",
                    )
                targets.append(
                    Target(
                        wallet=npubWallet,
                        source=source_wallet.wallet.id,
                        percent=entry.percent,
                        alias=entry.alias,
                        walletName=entry.wallet,
                    )
                )
            else:
                targets.append(
                    Target(
                        wallet=entry.wallet,
                        source=source_wallet.wallet.id,
                        percent=entry.percent,
                        alias=entry.alias,
                        walletName=entry.wallet,
                    )
                )

            percent_sum = sum([target.percent for target in targets])
            if percent_sum > 100:
                raise HTTPException(
                    status_code=HTTPStatus.BAD_REQUEST, detail="Splitting over 100%"
                )

        await set_targets(source_wallet.wallet.id, targets)

    except Exception as ex:
        logger.warning(ex)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Cannot set targets.",
        )


@splitpayments_ext.delete("/api/v1/targets", status_code=HTTPStatus.OK)
async def api_targets_delete(
    source_wallet: WalletTypeInfo = Depends(require_admin_key),
) -> None:
    await set_targets(source_wallet.wallet.id, [])


# deinit extension invoice listener
@splitpayments_ext.delete(
    "/api/v1", status_code=HTTPStatus.OK, dependencies=[Depends(check_admin)]
)
async def api_stop():
    for t in scheduled_tasks:
        try:
            t.cancel()
        except Exception as ex:
            logger.warning(ex)
    return {"success": True}
