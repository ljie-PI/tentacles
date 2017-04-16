package com.lab_440.tentacles.slave.parser;

import com.lab_440.tentacles.common.item.AbstractItem;
import com.lab_440.tentacles.common.ProcessStatus;

import java.util.List;

public interface IParser {

    public void init();

    public void parse(String url, String page) throws Exception;

    public ProcessStatus getStatus();

    public List<AbstractItem> getItems();

    public List<String> getFollowUrls();

}