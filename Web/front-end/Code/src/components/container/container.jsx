import './container.css';
import { useSelector } from 'react-redux';
import Cookies from 'js-cookie';
import Chatbot from '../../pages/chatBot/index';
import GuangkeBot from '../../pages/guangke/index';
import TCAD from '../../pages/tcad/index';
import ImageBot from '../../pages/fab/fabGPT';
import ClassIntroduce from '../../pages/classIntroduce/index';
import CircuitThink from '../../pages/CircuitThink/index';
import RagManager from '../RagManager';

function Container() {
  const mainPage = useSelector((state) => state.PageState.Main_Page);
  const subPage = useSelector((state) => state.PageState.Sub_Page);
  const currentUser = Cookies.get('user') || 'admin';

  return (
    <div className='container'>
      <div className='root-container'>
        {mainPage === 'ClassIntroduce' && <ClassIntroduce />}
        {mainPage === 'ChatBot' && subPage === "ChatBot" && <Chatbot port="5002" />}

        {mainPage === 'FabGPT' &&
          <>
            {subPage === "issue" && <ImageBot />}
            {subPage === "lithgraphy" && <GuangkeBot port="5003" />}
            {subPage === "tcad" && <TCAD port="5004" />}
            {subPage === "circuit" && <CircuitThink port="5007" />}
          </>
        }

        {mainPage === 'RagManager' && (
          <RagManager port="5100" userId={currentUser} />
        )}
      </div>
    </div>
  );
}

export default Container;
