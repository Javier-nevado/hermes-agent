<?php

namespace Kanboard\Plugin\OpteiaSkin\Helper;
use Kanboard\Core\Base;
use Kanboard\Plugin\OpteiaSkin\Plugin;

class BaseHelper extends Base
{
    protected function getPlugin(){
        return Plugin::getInstance($this->container);
    }
}
