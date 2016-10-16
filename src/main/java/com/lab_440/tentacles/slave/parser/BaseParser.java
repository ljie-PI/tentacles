package com.lab_440.tentacles.slave.parser;

import com.lab_440.tentacles.common.item.ResultItem;
import com.lab_440.tentacles.common.ProcessStatus;
import com.lab_440.tentacles.common.item.IItem;
import de.jetwick.snacktory.ArticleTextExtractor;
import de.jetwick.snacktory.JResult;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.PriorityQueue;
import java.util.regex.Pattern;

public class BaseParser implements IParser {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private String url;
    private String page;
    private ProcessStatus status;
    private List<IItem> items;
    private List<String> followUrls;
    private List<ParseMethod> parseMethods;
    private ArticleTextExtractor extractor;

    @Override
    public void init() {
        PriorityQueue<ParseMethod> priQueue
                = new PriorityQueue<>(64, (a, b) -> a.getPriority() - b.getPriority());
        Method[] methods = getClass().getMethods();
        for (int i = 0; i < methods.length; i++) {
            ParseRule annotation
                    = methods[i].getAnnotation(ParseRule.class);
            if (annotation != null) {
                ParseMethod parseMethod = new ParseMethod(methods[i],
                                                    annotation.priority(),
                                                    Pattern.compile(annotation.uriPattern()));
                priQueue.add(parseMethod);
            }
        }
        parseMethods = new ArrayList<>(priQueue.size());
        while (priQueue.size() > 0) {
            ParseMethod parseMethod = priQueue.poll();
            parseMethods.add(parseMethod);
        }
        extractor = new ArticleTextExtractor();
        items = new ArrayList<>();
        followUrls = new ArrayList<>();
    }

    @Override
    public void parse(String url, String page) throws Exception {
        this.url = url;
        this.page = page;
        items.clear();
        followUrls.clear();
        for (ParseMethod rule: parseMethods) {
            Pattern ptn = rule.getUriPattern();
            if (ptn.matcher(url).find()) {
                rule.getMethod().invoke(this);
                break;
            }
        }
    }

    @Override
    public ProcessStatus getStatus() {
        return status;
    }

    @Override
    public List<IItem> getItems() {
        return items;
    }

    @Override
    public List<String> getFollowUrls() {
        return followUrls;
    }

    @ParseRule(priority = 999, uriPattern = ".*")
    public void parseGeneral() throws Exception {
        JResult jres = extractor.extractContent(getPage());
        ResultItem item = new ResultItem();
        item.setUrl(getUrl());
        item.setTitle(jres.getTitle());
        item.setDate(jres.getDate());
        item.setContent(jres.getText());
        addItem(item);
    }

    public String getUrl() {
        return url;
    }

    public String getPage() {
        return page;
    }

    public void addItem(IItem item) {
        items.add(item);
    }

    public void followUrl(String url) {
        followUrls.add(url);
    }

    public void setStatus(ProcessStatus status) {
        this.status = status;
    }
}
