package com.lab_440.tentacles.app.downloaders;

import com.lab_440.tentacles.slave.downloader.BaseDownloader;
import com.lab_440.tentacles.slave.downloader.Processor;
import com.lab_440.tentacles.slave.downloader.Request;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

public class WeiboDownloader extends BaseDownloader {

    private Logger logger = LoggerFactory.getLogger(getClass());

    @Processor(priority = 10)
    public void appendCookie(Request request) {
        String cookie = "SCF=AvskEbivlLqxnXbZvC2zSR6D03fmcOl5q_uZnTxr8f4HueIoijH9I_Qocw3WsL798-lGyywVQF_wyo4jghHkZ0c.; " +
                "SSOLoginState=1476357735; " +
                "SUB=_2A256-x43DeTxGeBO6VEV-CnOyjiIHXVWB6J_rDV6PUJbkdBeLU_mkW0WrExc_6kT4x5X8HWc4GEQg6BYwg..; " +
                "SUHB=0M2FnaDmGNAJOg; _T_WM=d21af5290ffe864d305be40b8bf6e7dc; ";
        request.appendCookie(cookie);
    }
}
